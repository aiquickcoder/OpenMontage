"""Pre-clean: the footage of every city's best news candidates goes through the watermark-cleaner before anyone builds
a digest, so the films only cut ready pieces (owner, 2026-10-28: «в фоне ... чистятся на завтра»; 2026-10-29: more,
and in the daytime too).

    .venv/bin/python addons/news/preclean.py [--harvest] [--until 05:45] [--videos 6] [--photos 10] [--seconds 30]
                                             [--every 60]

Timers (MSK): news-preclean 21:30–05:45 (after the evening digests, before the morning ones) and news-preclean-day
10:00–16:45 (between the morning and the evening digests), both with /opt/openmontage/.env and
/opt/watermark-cleaner/deploy/.env.
1. --harvest: pick.py for every city first (fresh candidates + «добор»); when the list is done and time is left, it
   harvests again every --every minutes and cleans what is new (news keep coming in the night and the day).
2. Per city, in candidates.json order (fresh, then «добор»): up to --videos clips (their first --seconds) and --photos
   photos. Masks as in clean_footage.py: steady edges on a moving camera, known logo templates anywhere. A still camera
   with no known logo is skipped — it needs hand boxes when its story is built. A moving clip with nothing found is
   recorded as clean.
3. Results go to the cache (NEWS_CLEAN_CACHE, /srv/factory/news_clean_cache), keyed by the file's SHA-1:
   <key>.clean.mp4, <key>.json ({"len", "clean": "done"|"none", "how", "mask_area", "src"}), <key>.preview.jpg.
4. One job at a time, and none while a digest is being made (owner 2026-10-29: «если собирается видео, очистку на
   паузу»): a morning.py agent, produce.py, build.py or clean_footage.py --run on the server pauses us — the running
   job is cancelled at once (the cleaner has one worker and takes 1.5 of the 4 cores) and redone after. At --until
   nothing new is sent and the running job is cancelled.
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


BUSY = r"addons/news/(morning|produce|build)\.py|clean_footage\.py .*--run"  # a digest is being made


def digest_busy() -> bool:
    return bool(subprocess.run(["pgrep", "-f", BUSY], capture_output=True, text=True).stdout.strip())


def candidates(a: argparse.Namespace, py: str) -> list[Path]:
    todo: list[Path] = []
    for city in a.cities:
        base = Path(CITIES[city]["server_base"])
        if a.harvest:
            print(f"harvest {city}", flush=True)
            subprocess.run([py, "-I", str(HERE / "pick.py"), "--city", city, "--base", str(base), "--hours", "30",
                            "--top", "0"], stdout=subprocess.DEVNULL, check=False)
        if (base / "candidates.json").is_file():
            todo += media_of(base, 99 if a.keys else a.videos, 99 if a.keys else a.photos, set(a.keys or []) or None)
    return todo


def clean_one(cleaner: cf.Cleaner, path: Path, seconds: float, stop: datetime, skipped: set[Path]) -> None:
    """Clean one file into the cache; give way (cancel, not cached, tried again later) when a digest starts."""
    key = cf.digest(path)
    video = path.suffix.lower() in cf.VIDEO
    dur = float(cf.sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]) or 0) \
        if video else 0.0
    length = min(seconds, dur) if video else 0.0
    clip = cf.CACHE / f"{key}.in.mp4"
    try:
        cf.prepare(path, 0.0, length, clip)
    except subprocess.CalledProcessError:
        print(f"  skip {path.name}: ffmpeg could not read it", flush=True)
        skipped.add(path)
        return
    fr = cf.frames(path) if video else cf.frames(clip)
    if not fr:
        skipped.add(path)
        return
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
        clip.unlink(missing_ok=True)
        if still:  # nothing known found, and a still camera hides the rest: leave it to the build-time check
            print(f"  skip {path.name}: still camera, no known logo", flush=True)
            skipped.add(path)
            return
        (cf.CACHE / f"{key}.json").write_text(json.dumps({**meta, "clean": "none"}, ensure_ascii=False))
        print(f"  {path.name}: nothing to remove", flush=True)
        return
    jid = cleaner.submit(clip, mask)
    print(f"  cleaning {path.name} ({length:.0f} s, {how}, mask {meta['mask_area']:.1%})", flush=True)
    try:
        while True:
            if datetime.now(MSK) >= stop:
                cleaner.cancel(jid)
                print(f"  deadline: cancelled {path.name}", flush=True)
                return
            if digest_busy():
                cleaner.cancel(jid)
                print(f"  a digest is being made: {path.name} put back", flush=True)
                return
            j = cleaner.status(jid)
            if j["status"] == "succeeded":
                cleaner.download(jid, cf.CACHE / f"{key}.clean.mp4")
                (cf.CACHE / f"{key}.json").write_text(json.dumps({**meta, "clean": "done"}, ensure_ascii=False))
                print(f"  cleaned {path.name}", flush=True)
                return
            if j["status"] not in ("queued", "running"):
                print(f"  failed {path.name}: {j['status']} {j.get('error')}", flush=True)
                skipped.add(path)
                return
            time.sleep(15)
    finally:
        clip.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cities", nargs="*", default=sorted(CITIES))
    ap.add_argument("--harvest", action="store_true")
    ap.add_argument("--until", default="05:45")
    ap.add_argument("--videos", type=int, default=6)
    ap.add_argument("--photos", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--every", type=float, default=60.0, help="minutes between harvests while time is left")
    ap.add_argument("--keys", nargs="*", help="only these candidates (the stories already chosen for the next digests)")
    a = ap.parse_args()
    stop = deadline(a.until)
    cf.CACHE.mkdir(parents=True, exist_ok=True)
    py = str(HERE.parents[1] / ".venv" / "bin" / "python")
    cleaner, skipped = cf.Cleaner(), set()
    while True:
        harvested = time.time()
        for path in candidates(a, py):
            if datetime.now(MSK) >= stop:
                break
            if path in skipped or cf.cached(path):  # done (after the newest logo template) or hopeless
                continue
            if digest_busy():
                print("  a digest is being made: paused", flush=True)
                while digest_busy() and datetime.now(MSK) < stop:
                    time.sleep(60)
                print("  resumed", flush=True)
            clean_one(cleaner, path, a.seconds, stop, skipped)
        left = (stop - datetime.now(MSK)).total_seconds()
        if not a.harvest or a.keys or left < 20 * 60:
            break
        nap = min(max(0.0, harvested + a.every * 60 - time.time()), left - 20 * 60)
        print(f"list done; next harvest in {nap / 60:.0f} min", flush=True)
        time.sleep(nap)
    print("PRECLEAN DONE", flush=True)


if __name__ == "__main__":
    main()
