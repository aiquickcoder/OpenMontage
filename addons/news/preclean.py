"""Night pre-clean: the footage of every city's best news candidates goes through the watermark-cleaner before anyone
builds a digest, so tomorrow's films only cut ready pieces (owner, 2026-10-28: «в фоне ... чистятся на завтра»).

    .venv/bin/python addons/news/preclean.py [--harvest] [--until 07:00] [--videos 4] [--photos 6] [--seconds 30]

Run by the news-preclean.timer every night (01:00 MSK) with /opt/openmontage/.env and /opt/watermark-cleaner/deploy/.env.
1. --harvest: pick.py for every city first (fresh candidates + «добор»).
2. Per city, in candidates.json order (fresh, then «добор»): up to --videos clips (their first --seconds) and --photos
   photos. Masks as in clean_footage.py: steady edges on a moving camera, known logo templates anywhere. A still camera
   with no known logo is skipped — it needs hand boxes when its story is built. A moving clip with nothing found is
   recorded as clean.
3. Results go to the cache (NEWS_CLEAN_CACHE, /srv/factory/news_clean_cache), keyed by the file's SHA-1:
   <key>.clean.mp4, <key>.json ({"len", "clean": "done"|"none", "how", "mask_area", "src"}), <key>.preview.jpg.
4. At --until (MSK, 05:45 — the 06:00 digests need the cleaner) nothing new is sent and queued jobs are cancelled: the morning digests must not wait behind the night.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from addons.news import clean_footage as cf  # noqa: E402

MSK = timezone(timedelta(hours=3))
CITIES = json.loads((HERE / "cities.json").read_text(encoding="utf-8"))


def deadline(until: str) -> datetime:
    h, m = map(int, until.split(":"))
    now = datetime.now(MSK)
    d = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return d if d > now else d + timedelta(days=1)


def media_of(base: Path, videos: int, photos: int, keys: set[str] | None = None) -> list[Path]:
    cand = json.loads((base / "candidates.json").read_text(encoding="utf-8"))
    vids, pics, seen = [], [], set()
    for c in cand.get("candidates", []) + cand.get("fill", []):
        if keys and c.get("key") not in keys:
            continue
        for m in c.get("media_local") or []:
            p = Path(m)
            if not p.is_file() or p in seen:
                continue
            seen.add(p)
            (vids if p.suffix.lower() in cf.VIDEO else pics).append(p)
    return vids[:videos] + pics[:photos]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cities", nargs="*", default=sorted(CITIES))
    ap.add_argument("--harvest", action="store_true")
    ap.add_argument("--until", default="05:45")
    ap.add_argument("--videos", type=int, default=4)
    ap.add_argument("--photos", type=int, default=6)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--keys", nargs="*", help="only these candidates (the stories already chosen for the next digests)")
    a = ap.parse_args()
    stop = deadline(a.until)
    cf.CACHE.mkdir(parents=True, exist_ok=True)
    py = str(HERE.parents[1] / ".venv" / "bin" / "python")
    todo: list[Path] = []
    for city in a.cities:
        base = Path(CITIES[city]["server_base"])
        if a.harvest:
            print(f"harvest {city}", flush=True)
            subprocess.run([py, "-I", str(HERE / "pick.py"), "--city", city, "--base", str(base), "--hours", "30",
                            "--top", "0"], stdout=subprocess.DEVNULL, check=False)
        if (base / "candidates.json").is_file():
            todo += media_of(base, 99 if a.keys else a.videos, 99 if a.keys else a.photos, set(a.keys or []) or None)
    cleaner, jobs = cf.Cleaner(), {}  # job id -> (cache key, meta)
    for path in todo:
        if datetime.now(MSK) >= stop:
            print("deadline: nothing new is sent", flush=True)
            break
        key = cf.digest(path)
        if cf.cached(path):  # done, and after the newest logo template
            continue
        video = path.suffix.lower() in cf.VIDEO
        dur = float(cf.sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]) or 0) \
            if video else 0.0
        length = min(a.seconds, dur) if video else 0.0
        clip = cf.CACHE / f"{key}.in.mp4"
        try:
            cf.prepare(path, 0.0, length, clip)
        except subprocess.CalledProcessError:
            print(f"  skip {path.name}: ffmpeg could not read it", flush=True)
            continue
        fr = cf.frames(path) if video else cf.frames(clip)
        if not fr:
            continue
        mask, moving, blind = cf.auto_mask(fr) if len(fr) > 2 else (np.zeros(fr[0].shape[:2], np.uint8), 0.0, 0.0)
        still = moving < cf.STILL_SHIFT
        if still:
            mask = np.zeros_like(mask)
        mask = np.maximum(mask, cf.template_mask(fr))
        how = ("still camera, known logos" if still else f"auto, camera {moving:.1f} px/frame")
        # blind: the day build will not use this clean until someone has looked (clean_footage.py, NEEDS A LOOK)
        meta = {"len": round(length, 3), "how": how, "mask_area": round(float((mask > 0).mean()), 4), "src": str(path),
                "blind": cf.needs_look(moving, blind)}
        cv2.imwrite(str(cf.CACHE / f"{key}.preview.jpg"), cf.preview(fr, mask))
        if not np.any(mask):
            if still:  # nothing known found, and a still camera hides the rest: leave it to the build-time check
                print(f"  skip {path.name}: still camera, no known logo", flush=True)
                clip.unlink(missing_ok=True)
                continue
            (cf.CACHE / f"{key}.json").write_text(json.dumps({**meta, "clean": "none"}, ensure_ascii=False))
            print(f"  {path.name}: nothing to remove", flush=True)
            continue
        jobs[cleaner.submit(clip, mask)] = (key, meta)
        print(f"  queued {path.name} ({length:.0f} s, {how}, mask {meta['mask_area']:.1%})", flush=True)
    while jobs:
        if datetime.now(MSK) >= stop:
            for jid in jobs:
                cleaner.cancel(jid)
            print(f"deadline: cancelled {len(jobs)} queued/running jobs", flush=True)
            break
        for jid, (key, meta) in list(jobs.items()):
            j = cleaner.status(jid)
            if j["status"] in ("queued", "running"):
                continue
            if j["status"] == "succeeded":
                cleaner.download(jid, cf.CACHE / f"{key}.clean.mp4")
                (cf.CACHE / f"{key}.json").write_text(json.dumps({**meta, "clean": "done"}, ensure_ascii=False))
                print(f"  cleaned {Path(meta['src']).name}", flush=True)
            else:
                print(f"  failed {Path(meta['src']).name}: {j['status']} {j.get('error')}", flush=True)
            (cf.CACHE / f"{key}.in.mp4").unlink(missing_ok=True)
            del jobs[jid]
        time.sleep(20)
    print("PRECLEAN DONE", flush=True)


if __name__ == "__main__":
    main()
