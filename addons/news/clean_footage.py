"""Wipe other channels' watermarks from a digest's post footage before the build (owner, 2026-10-28).

    .venv/bin/python addons/news/clean_footage.py projects/<slug> --plan     # masks + previews, nothing sent
    .venv/bin/python addons/news/clean_footage.py projects/<slug> --run      # send, wait, swap the shots

Every post video and post photo the story uses goes through the watermark-cleaner service on this server
(/opt/watermark-cleaner, API on 127.0.0.1:8786, HTTP Basic from its deploy/.env: APP_USERNAME / APP_PASSWORD — never
printed). Commons photos (credit mentions Wikimedia) and our own illustrations are skipped.

Masks. A watermark is the one thing whose edges point the same way in every frame while the picture moves, even when
it is semi-transparent (Region 116, ГОСАВТОИНСПЕКЦИЯ КАЗАНИ): the per-pixel sum of Sobel vectors over ~90 frames keeps
its contours and cancels a moving background. On a still camera (CCTV, a traffic camera, a photo) the whole scene is
"steady", so the auto mask is not trusted there and the agent gives boxes by hand. Known logos are also found by
template (addons/news/watermarks/*.png, gradient matching at several scales). Hand boxes go into
projects/<slug>/media/wm.json: {"<file>": [[x, y, w, h], ...]} in source pixels; [] = checked, nothing to remove.
--plan writes qa/wm_<file>.jpg (the mask in red over a frame) — look at every one before --run; --grid FILE draws a
labelled pixel grid over a frame for hand boxes. A logo met again later: --save-template NAME FILE X Y W H.
--run marks every post-media shot: cleaned ones point at media/clean/, clean ones get "clean": "none"; build.py refuses
post footage that has neither (owner, 2026-10-28: no foreign watermarks in the digests).

Only the seconds a shot shows are cleaned (from its "in": 3.4 s, the last shot of a story 6 s — it is held longer):
the service needs about a minute of CPU per second of 720p footage. --run writes media/clean/<file>_<in>.mp4 per shot,
points the shot there ("in": 0) and keeps the original path in "src_orig"; story.json.pre-clean is the backup.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "watermarks"
# addons/news/preclean.py cleans the best candidates' footage at night; a piece cut from there needs no service job
CACHE = Path(os.environ.get("NEWS_CLEAN_CACHE", "/srv/factory/news_clean_cache"))
SERVICE = "http://127.0.0.1:8786"
SHOT_S, LAST_S = 3.4, 6.0   # a shot lasts 3 s (+ a margin); the last one of a story is held longer
EDGE_THR = 12.0       # consistent edge strength: Region 116 at 12, a moving background stays under it
TEMPLATE_MIN = 0.5    # normalised correlation of steady-edge maps that counts as the same logo
TEMPLATE_TEXT_MIN = 0.6  # a lettering template (name contains "text") looks like any text: stricter
STILL_SHIFT = 0.8     # px: median frame-to-frame camera shift below it = a still camera (CCTV, traffic camera)
VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def sh(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def frames(path: Path, n: int = 90) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    out = []
    for i in np.linspace(0, total - 1, min(n, total)).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if ok:
            out.append(f)
    cap.release()
    return out


def steady_edges(fr: list[np.ndarray]) -> np.ndarray:
    """Per-pixel length of the summed Sobel vectors / frames: overlay contours stay, a moving picture cancels out."""
    g = [cv2.GaussianBlur(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32), (3, 3), 0) for f in fr]
    sx = sum(cv2.Sobel(x, cv2.CV_32F, 1, 0) for x in g)
    sy = sum(cv2.Sobel(x, cv2.CV_32F, 0, 1) for x in g)
    return cv2.magnitude(sx, sy) / len(g)


def camera_shift(fr: list[np.ndarray]) -> float:
    """Median global shift between neighbouring sampled frames, px. Cars moving under a fixed camera do not count."""
    small = [cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (320, int(320 * f.shape[0] / f.shape[1]))).astype(np.float32)
             for f in fr]
    shifts = [np.hypot(*cv2.phaseCorrelate(a, b)[0]) * fr[0].shape[1] / 320 for a, b in zip(small, small[1:])]
    return float(np.median(shifts)) if shifts else 0.0


def auto_mask(fr: list[np.ndarray]) -> tuple[np.ndarray, float, float]:
    """Steady-edge mask of a clip, its median camera shift (px) and its blind share: the frame share of steady patches
    too big to be a logo. A slow camera keeps the buildings steady too, a logo merges with them and the patch is
    dropped whole — «фонтанка.ру» across theatre.mp4 (SPb 28.10). Blind > 0: somebody has to look (needs_look)."""
    strength = steady_edges(fr)
    moving = camera_shift(fr)
    cand = cv2.morphologyEx((strength > EDGE_THR).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((13, 13), np.uint8))
    h, w = cand.shape
    n, lab, st, _ = cv2.connectedComponentsWithStats(cand)
    mask, blind = np.zeros((h, w), np.uint8), 0.0
    for i in range(1, n):
        x, y, bw, bh, a = st[i]
        if a > 150 and bw * bh < 0.12 * w * h:
            mask[lab == i] = 255
        elif a > 150:
            blind = max(blind, bw * bh / (w * h))
    return cv2.dilate(mask, np.ones((9, 9), np.uint8)), moving, round(float(blind), 3)


def needs_look(moving: float, blind: float) -> bool:
    """The auto mask cannot be trusted: a still camera, or a steady background it cannot tell logos from."""
    return moving < STILL_SHIFT or blind > 0


def hand_mask(shape: tuple[int, int], entry, auto: np.ndarray) -> np.ndarray:
    """A media/wm.json entry: a list of boxes replaces the auto mask (a still camera), {"add": [...]} adds boxes to it
    ({"add": []} = looked, nothing else to remove)."""
    if isinstance(entry, dict):
        return np.maximum(auto, boxes_mask(shape, entry.get("add", [])))
    return boxes_mask(shape, entry)


def preview(fr: list[np.ndarray], mask: np.ndarray) -> np.ndarray:
    """qa/wm_<file>.jpg: a middle frame and the clip's average side by side, the mask in red. On the average the
    picture smears and a fixed overlay stays sharp, so a logo the mask misses shows up there."""
    acc = np.zeros(fr[0].shape, np.float32)
    step = max(1, len(fr) // 40)
    for f in fr[::step]:
        acc += f
    avg = (acc / len(fr[::step])).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    avg = cv2.cvtColor(clahe.apply(cv2.cvtColor(avg, cv2.COLOR_BGR2GRAY)), cv2.COLOR_GRAY2BGR)
    out = []
    for img in (fr[len(fr) // 2].copy(), avg):
        img[mask > 0] = (0.35 * img[mask > 0] + 0.65 * np.array([0, 0, 255])).astype(np.uint8)
        out.append(img)
    vis = np.hstack(out)
    return cv2.resize(vis, None, fx=min(1.0, 1600 / vis.shape[1]), fy=min(1.0, 1600 / vis.shape[1]))


def template_mask(fr: list[np.ndarray]) -> np.ndarray:
    """Known logos (addons/news/watermarks/*.png — steady-edge crops saved by --save-template) found in the clip's own
    steady-edge map at several scales: one frame is too noisy for a semi-transparent logo, the map is not."""
    h, w = fr[0].shape[:2]
    mask = np.zeros((h, w), np.uint8)
    fg = np.clip(steady_edges(fr) * 4, 0, 255).astype(np.float32)
    for p in sorted(TEMPLATES.glob("*.png")):
        t = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if t is None:
            continue
        best = (0.0, None)  # one match per logo: the best over all scales
        for s in (0.5, 0.6, 0.67, 0.75, 0.85, 1.0, 1.15, 1.3, 1.5, 1.75, 2.0):
            tw, th = int(t.shape[1] * s), int(t.shape[0] * s)
            if tw < 16 or th < 16 or tw >= w or th >= h:
                continue
            r = cv2.matchTemplate(fg, cv2.resize(t, (tw, th)).astype(np.float32), cv2.TM_CCOEFF_NORMED)
            _, score, _, (x, y) = cv2.minMaxLoc(r)
            if score > best[0]:
                best = (score, (x, y, tw, th))
        if os.environ.get("WM_DEBUG"):
            print(f"    template {p.stem}: {best[0]:.2f} at {best[1]}", flush=True)
        if best[0] > (TEMPLATE_TEXT_MIN if "text" in p.stem else TEMPLATE_MIN):
            x, y, tw, th = best[1]
            mask[max(0, y - 6):y + th + 6, max(0, x - 6):x + tw + 6] = 255
    return mask


def boxes_mask(shape: tuple[int, int], boxes: list[list[int]]) -> np.ndarray:
    mask = np.zeros(shape, np.uint8)
    for x, y, w, h in boxes:
        mask[max(0, y):y + h, max(0, x):x + w] = 255
    return mask


def digest(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()[:20]


def templates_changed() -> float:
    return max((t.stat().st_mtime for t in TEMPLATES.glob("*.png")), default=0.0)


def cached(path: Path) -> dict | None:
    """The night pre-clean of this exact file: {"len": cleaned seconds from 0, "clean": "done"|"none", ...}.
    One made before a logo template was added never looked for that logo, so it does not count."""
    meta = CACHE / f"{digest(path)}.json"
    if not meta.is_file() or meta.stat().st_mtime < templates_changed():
        return None
    return {**json.loads(meta.read_text()), "key": meta.stem}


def even(n: int) -> int:
    return n - n % 2


def seconds(path: Path) -> float:
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]) or 0)


def prepare(src: Path, start: float, dur: float, dst: Path) -> None:
    """A service-friendly copy: CFR 30, 8-bit 4:2:0, even size, no audio (footage is silent anyway)."""
    vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"
    if src.suffix.lower() in VIDEO:
        sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}", "-an", "-r", "30",
            "-vf", vf, "-c:v", "libx264", "-crf", "14", "-preset", "veryfast", str(dst)])
    else:  # a photo becomes a 2-frame clip
        sh(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(src), "-frames:v", "2", "-r", "2", "-vf", vf,
            "-c:v", "libx264", "-crf", "12", str(dst)])


class Cleaner:
    def __init__(self) -> None:
        user, pw = os.environ.get("APP_USERNAME"), os.environ.get("APP_PASSWORD")
        if not user or not pw:
            sys.exit("APP_USERNAME / APP_PASSWORD missing — run with EnvironmentFile=/opt/watermark-cleaner/deploy/.env")
        self.auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()

    def call(self, path: str, body: bytes | None = None, ctype: str | None = None) -> bytes:
        h = {"Authorization": self.auth, **({"Content-Type": ctype} if ctype else {})}
        req = urllib.request.Request(SERVICE + path, data=body, method="POST" if body is not None else "GET", headers=h)
        with urllib.request.urlopen(req, timeout=900) as r:
            return r.read()

    @staticmethod
    def form(fields: dict, files: dict) -> tuple[bytes, str]:
        b = uuid.uuid4().hex
        body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
        for k, (name, data, ctype) in files.items():
            body += (f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                     f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n"
        return body + f"--{b}--\r\n".encode(), f"multipart/form-data; boundary={b}"

    def submit(self, clip: Path, mask: np.ndarray) -> str:
        up = json.loads(self.call("/api/uploads", *self.form({}, {"file": (clip.name, clip.read_bytes(), "video/mp4")})))
        if (up["height"], up["width"]) != mask.shape:
            mask = cv2.resize(mask, (up["width"], up["height"]), interpolation=cv2.INTER_NEAREST)
        png = cv2.imencode(".png", mask)[1].tobytes()
        body = self.form({"upload_id": up["id"], "profile_id": "custom", "enhancement": "none"},
                         {"mask_file": ("mask.png", png, "image/png")})
        while True:
            try:
                return json.loads(self.call("/api/jobs", *body))["id"]
            except urllib.error.HTTPError as e:  # the service keeps at most 10 jobs: wait for a free place
                if e.code != 429:
                    raise
                time.sleep(20)

    def status(self, jid: str) -> dict:
        return json.loads(self.call(f"/api/jobs/{jid}"))

    def download(self, jid: str, out: Path) -> None:
        out.write_bytes(self.call(f"/api/jobs/{jid}/download"))

    def cancel(self, jid: str) -> None:
        self.call(f"/api/jobs/{jid}/cancel", b"", "application/x-www-form-urlencoded")

    def wait(self, jobs: dict[str, Path]) -> None:
        """jobs: id -> output path. Downloads each result as soon as it is ready."""
        t0, left = time.time(), dict(jobs)
        while left:
            for jid, out in list(left.items()):
                j = json.loads(self.call(f"/api/jobs/{jid}"))
                if j["status"] in ("queued", "running"):
                    continue
                if j["status"] != "succeeded":
                    raise RuntimeError(f"{out.name}: {j['status']} {j.get('error')}")
                out.write_bytes(self.call(f"/api/jobs/{jid}/download"))
                print(f"  cleaned {out.name} ({time.time() - t0:.0f} s since start)", flush=True)
                del left[jid]
            if left:
                time.sleep(10)


def footage(proj: Path, story: dict) -> dict[str, dict]:
    """Pieces of post media the story shows: "<file>@<in>" -> {"src", "start", "len"}; a photo is one piece.
    With a shot per phrase (assets/beats.json from build.py --voice-only) a piece is as long as its phrase, else
    SHOT_S (the last shot of a story LAST_S)."""
    use: dict[str, dict] = {}
    beats_file = proj / "assets" / "beats.json"
    phrases = json.loads(beats_file.read_text(encoding="utf-8")) if beats_file.is_file() else []
    for si, st in enumerate(story.get("stories", [])):
        shots = st.get("shots", [])
        bs = phrases[si] if si < len(phrases) and phrases[si] and len(shots) >= len(phrases[si]) else None
        for j, sh_ in enumerate(shots):
            if bs is not None and j >= len(bs):  # past the last phrase: never shown
                continue
            src = sh_.get("src_orig") or sh_.get("src", "")
            if not src.startswith("media/") or "Wikimedia" in (sh_.get("credit") or "") or "/clean/" in src:
                continue
            video = Path(src).suffix.lower() in VIDEO
            t = float(sh_.get("in_orig", sh_.get("in", 0.0))) if video else 0.0
            if bs is not None:
                length = round(bs[j]["end"] - bs[j]["start"] + 0.3, 2) if video else 0.0
            else:
                length = (LAST_S if j == len(shots) - 1 else SHOT_S) if video else 0.0
            u = use.setdefault(f"{src}@{t:g}", {"src": src, "start": t, "len": 0.0})
            u["len"] = max(u["len"], length)
    return use


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--run", action="store_true")
    g.add_argument("--grid", metavar="FILE", help="qa/grid_<FILE>.jpg: a frame with a labelled pixel grid, for hand boxes")
    g.add_argument("--save-template", nargs=6, metavar=("NAME", "FILE", "X", "Y", "W", "H"),
                   help="remember a logo: steady edges of media/FILE inside the box -> addons/news/watermarks/NAME.png")
    a = ap.parse_args()
    proj = a.project
    if a.grid:
        src = proj / "media" / a.grid
        f = cv2.imread(str(src)) if src.suffix.lower() not in VIDEO else frames(src, 3)[1]
        h, w = f.shape[:2]
        step = 80 if max(w, h) > 900 else 50
        for x in range(0, w, step):
            cv2.line(f, (x, 0), (x, h), (0, 255, 255), 1)
            cv2.putText(f, str(x), (x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        for y in range(0, h, step):
            cv2.line(f, (0, y), (w, y), (0, 255, 255), 1)
            cv2.putText(f, str(y), (2, y - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        (proj / "qa").mkdir(exist_ok=True)
        cv2.imwrite(str(proj / "qa" / f"grid_{src.stem}.jpg"), f)
        print(f"{proj / 'qa' / ('grid_' + src.stem + '.jpg')} ({w}x{h})")
        return
    if a.save_template:
        name, file, *box = a.save_template
        x, y, w, h = map(int, box)
        crop = steady_edges(frames(proj / "media" / file))[y:y + h, x:x + w]
        TEMPLATES.mkdir(exist_ok=True)
        cv2.imwrite(str(TEMPLATES / f"{name}.png"), np.clip(crop * 4, 0, 255).astype(np.uint8))
        print(f"saved {TEMPLATES / (name + '.png')}")
        return
    story = json.loads((proj / "story.json").read_text(encoding="utf-8"))
    hand = json.loads((proj / "media" / "wm.json").read_text()) if (proj / "media" / "wm.json").is_file() else {}
    work, qa_dir = proj / "media" / "clean", proj / "qa"
    work.mkdir(parents=True, exist_ok=True)
    qa_dir.mkdir(exist_ok=True)
    plan: dict[str, dict] = {}
    masks: dict[str, np.ndarray] = {}  # one mask per source file, from the whole clip (more frames, steadier)
    for key, piece in footage(proj, story).items():
        src = piece["src"]
        path = proj / src
        is_video = path.suffix.lower() in VIDEO
        dur = 0.0
        if is_video:
            dur = float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]) or 0)
        start = min(piece["start"], max(0.0, dur - 0.5)) if is_video else 0.0
        length = min(piece["len"], dur - start) if is_video else 0.0
        clip = work / f"{Path(src).stem}_{start:g}.in.mp4"
        name = Path(src).name
        entry = hand.get(name)
        night = cached(path) if entry is None or entry == {"add": []} else None
        if night and (not is_video or start + length <= night["len"] + 0.05):
            out = work / f"{Path(src).stem}_{start:g}.clean.mp4"
            if night["clean"] == "done" and (not out.is_file() or (is_video and seconds(out) < length - 0.1)):
                if is_video:
                    sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(CACHE / f"{night['key']}.clean.mp4"),
                        "-t", f"{length:.3f}", "-an", "-c:v", "libx264", "-crf", "14", "-preset", "veryfast", str(out)])
                else:
                    shutil.copy2(CACHE / f"{night['key']}.clean.mp4", out)
            if (CACHE / f"{night['key']}.preview.jpg").is_file():
                shutil.copy2(CACHE / f"{night['key']}.preview.jpg", qa_dir / f"wm_{Path(src).stem}.jpg")
            # a night clean of a clip the auto mask is blind on (or an entry from before that was recorded) is
            # used only once somebody looked: the night run has no eyes
            look = entry is None and night.get("blind", True)
            plan[key] = {"src": src, "clip": clip, "start": start, "video": is_video, "how": f"pre-cleaned at night ({night.get('how', '')})"
                         + (" — NEEDS A LOOK" if look else ""), "unchecked": look, "night": night["clean"]}
            if night["clean"] == "done":
                plan[key]["out_mp4"] = out
            print(f"{key}: {plan[key]['how']}, window {start:.1f}–{start + length:.1f} s", flush=True)
            continue
        prepare(path, start, length, clip)
        if src in masks:
            plan[key] = {**plan[next(k for k in plan if plan[k]["src"] == src)], "clip": clip, "start": start}
            print(f"{key}: same mask as {src}, window {start:.1f}–{start + length:.1f} s", flush=True)
            continue
        fr = frames(path) if is_video else frames(clip)
        mask, moving, blind = auto_mask(fr) if len(fr) > 2 else (np.zeros(fr[0].shape[:2], np.uint8), 0.0, 0.0)
        how = f"auto (camera moves {moving:.1f} px/frame)"
        if moving < STILL_SHIFT:
            mask, how = np.zeros_like(mask), f"still camera ({moving:.1f} px)"
        elif blind:
            how += f", steady background over {blind:.0%} of the frame — the auto mask is blind there"
        if entry is not None:
            mask = hand_mask(fr[0].shape[:2], entry, mask)
            how += " + hand boxes" if isinstance(entry, dict) else " → hand boxes"
        elif needs_look(moving, blind):
            how += " — NEEDS A LOOK"
        known = template_mask(fr)
        if np.any(known):
            how += " + known logos"
        mask = np.maximum(mask, known)
        area = float((mask > 0).mean())
        cv2.imwrite(str(qa_dir / f"wm_{Path(src).stem}.jpg"), preview(fr, mask))
        cv2.imwrite(str(work / (Path(src).stem + ".mask.png")), mask)
        masks[src] = mask
        plan[key] = {"src": src, "clip": clip, "start": start, "video": is_video, "mask_area": area, "how": how,
                     "unchecked": entry is None and needs_look(moving, blind)}
        print(f"{key}: {how}, mask {area:.1%}, window {start:.1f}–{start + length:.1f} s", flush=True)
    if a.plan:
        print("look at qa/wm_*.jpg; fix with media/wm.json, then --run")
        return
    unchecked = sorted({p["src"] for p in plan.values() if p["unchecked"]})
    if unchecked:  # the auto mask cannot see logos here: someone has to look first (owner 2026-10-29: never miss one)
        sys.exit("NEEDS A LOOK before cleaning: " + ", ".join(unchecked) + ". Open qa/wm_<file>.jpg (left a frame, right "
                 "the clip average where a fixed logo stays sharp; red = removed). In media/wm.json give each file "
                 "{\"add\": []} if nothing is left, {\"add\": [[x, y, w, h]]} for a logo the red misses (--grid <file> "
                 "for coordinates), a plain list of boxes for a still camera ([] = clean). A channel logo that will "
                 "come again: also --save-template <channel>_logo|<channel>_text <file> x y w h.")
    cleaner, jobs, sigs = Cleaner(), {}, {}
    for key, p in plan.items():
        src = p["src"]
        if p.get("night"):  # cut from the night pre-clean (or found clean at night)
            if p["night"] == "none":
                print(f"  {key}: no watermark (night check) — kept as is", flush=True)
            continue
        mask = cv2.imread(str(work / (Path(src).stem + ".mask.png")), cv2.IMREAD_GRAYSCALE)
        if not np.any(mask):
            print(f"  {key}: no watermark to remove — kept as is", flush=True)
            continue
        p["out_mp4"] = work / f"{Path(src).stem}_{p['start']:g}.clean.mp4"
        # a rerun keeps what is done, but only if it was cleaned with this very mask: pieces cleaned before the
        # mask learned the Region 116 emblem kept it after the mask was fixed (Kazan 28.10, owner's screenshot)
        sig, sigs[p["out_mp4"]] = p["out_mp4"].with_suffix(".mask.sha1"), hashlib.sha1(mask.tobytes()).hexdigest()
        # …and only if it is as long as the shot now needs (a phrase can be longer than the old 3.4 s piece)
        if not (p["out_mp4"].is_file() and sig.is_file() and sig.read_text() == sigs[p["out_mp4"]]
                and (not p["video"] or seconds(p["out_mp4"]) >= seconds(p["clip"]) - 0.1)):
            jobs[cleaner.submit(p["clip"], mask)] = p["out_mp4"]
    print(f"queued {len(jobs)} pieces", flush=True)
    cleaner.wait(jobs)
    for out in jobs.values():
        if out.is_file():
            out.with_suffix(".mask.sha1").write_text(sigs[out])
    for key, p in plan.items():
        if "out_mp4" not in p:
            continue
        src, out = p["src"], p["out_mp4"]
        if not p["video"]:
            still = work / (Path(src).stem + ".clean.jpg")
            sh(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-frames:v", "1", "-q:v", "2", str(still)])
            out = still
        p["out"] = out.relative_to(proj).as_posix()
    shutil.copy2(proj / "story.json", proj / "story.json.pre-clean")
    for st in story.get("stories", []):
        for s in st.get("shots", []):
            src = s.get("src_orig") or s.get("src")
            video = Path(src).suffix.lower() in VIDEO
            t0 = float(s.get("in_orig", s.get("in", 0.0))) if video else 0.0
            p = plan.get(f"{src}@{t0:g}")
            if not p:
                continue
            if p.get("out"):
                s["src_orig"], s["src"] = src, p["out"]
                if video:
                    s["in_orig"], s["in"] = t0, round(t0 - p["start"], 3)
                s.pop("clean", None)
            else:  # checked: nothing to remove — build.py accepts the original
                s["clean"] = "none"
    (proj / "story.json").write_text(json.dumps(story, ensure_ascii=False, indent=1), encoding="utf-8")
    print("DONE story.json now points at media/clean/")


if __name__ == "__main__":
    main()
