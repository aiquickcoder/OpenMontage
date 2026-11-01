"""Pictures for a post: the best photos/videos of the story, made free of other outlets' marks — or none at all.

Owner's rule: no foreign logo ever goes out. A picture that cannot be made reliably clean is replaced by another
source's picture of the same story, or the post goes without one («лучше без картинки, чем с чужим знаком»).

For each candidate file:
1. Look. Two vision models (model_vision + model_check) mark every overlay — logos, watermarks (faint ones too),
   @names, URLs, channel names, burned-in captions, app UI — on the photo, or on a video's frames at 3/15/50/85/97 %
   plus the average of all frames (a fixed overlay stays sharp there while the picture smears). Screenshots (TV,
   sites, social apps) and graphics (cards, memes, maps) are not taken. A branded intro/outro card is trimmed off.
   On a moving camera the steady-edge mask of addons/news/clean_footage.py and the known-logo templates add what the
   eyes may miss; a faint-text map (per-pixel minimum over 2-second windows, high frequencies) adds faint lettering.
2. Clean. Marks within an edge band (crop_max_share of the frame) are cropped off — instant, no smear. Anything else
   goes to the watermark-cleaner service (MI-GAN): photos always, videos only up to inpaint_video_max_s seconds and
   only while no news film is being made on the server (the films come first); the original sound is put back.
3. Check. The result is looked at again by both models; anything still there and the file is dropped.
"""
from __future__ import annotations

import concurrent.futures as cfut
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from addons.channels import config, llm
from addons.news import clean_footage as cf

VIDEO = cf.VIDEO
VISION = """You inspect a news photo or a video frame before a news channel republishes it. Find EVERY overlay that
was added on top of the picture by another outlet or app: channel/TV/site logos, watermarks (also faint,
semi-transparent, small in a corner), @usernames, URLs, channel names, burned-in captions, name bars, subtitles,
stickers, app UI (Instagram/TikTok buttons, comments, likes).
NOT overlays: text that is part of the real scene (street signs, shop signs, car plates, uniforms, screens filmed by
the camera) and plain CCTV/dashcam date-time stamps without a brand.
Classify the whole image: "photo" (a real camera picture or video frame), "screenshot" (of TV, a website, a social
app, a messenger), "graphic" (a card, infographic, meme, poster, map, document, a branded intro/outro screen).
Answer JSON only: {"kind": "photo|screenshot|graphic", "overlays": [{"what": "short description with its text",
"box": [ymin, xmin, ymax, xmax]}], "clean": true|false}
Boxes in 0..1000 of the image height/width; generous (the whole mark with a margin). clean = no overlays at all."""
AVG_NOTE = ("This is the AVERAGE of all frames of a video (contrast-boosted): moving content smears, while overlays "
            "fixed on the screen stay sharp. Report only marks that are sharp here.")

NEWS_BUSY = r"addons/news/(morning|produce|build)\.py|clean_footage\.py .*--run"


class Reject(Exception):
    pass


class Slow(Reject):
    """The file needs the cleaner (minutes to an hour) and the caller wanted only the fast ways."""


# -- looking ------------------------------------------------------------------------------------------------------
def vision_models(db) -> list[str]:
    a, b = config.setting(db, "model_vision"), config.setting(db, "model_vision2") or config.setting(db, "model_vision")
    return [a] if a == b else [a, b]


MANY = VISION.replace("Classify the whole image", "Classify each whole image").replace(
    'Answer JSON only: {"kind"', 'You get several images, numbered. Answer JSON only, one entry per image: '
    '{"images": [{"n": 1, "kind"').replace('"clean": true|false}', '"clean": true|false}]}')


def look_many(db, imgs: list[tuple[np.ndarray, str]]) -> list[dict]:
    """One request for several images of one file (frames, the average): per image {"kind", "boxes", "what"} with
    pixel boxes; with two vision models, the union of what both saw and the stricter kind."""
    parts: list[dict] = [{"type": "text", "text": "Inspect each image."}]
    for i, (img, note) in enumerate(imgs, 1):
        parts.append({"type": "text", "text": f"Image {i}." + (f" {note}" if note else "")})
        parts.append(llm.image_part_array(img, 1024))

    def one(model: str):
        if len(imgs) == 1:
            r = llm.chat_json(db, "vision", model, VISION, parts, max_tokens=900, temperature=0)
            return [r] if isinstance(r, dict) else r
        r = llm.chat_json(db, "vision", model, MANY, parts, max_tokens=500 * len(imgs), temperature=0)
        return (r.get("images") if isinstance(r, dict) else r) or []

    models = vision_models(db)
    with cfut.ThreadPoolExecutor(len(models)) as ex:
        answers = list(ex.map(lambda m: _safe(one, m), models))
    out = [{"kind": "photo", "boxes": [], "what": []} for _ in imgs]
    seen = 0
    for ans in answers:
        if not isinstance(ans, list):
            continue
        seen += 1
        for j, r in enumerate(ans):
            if not isinstance(r, dict):
                continue
            k = int(r.get("n") or j + 1) - 1 if str(r.get("n", "")).isdigit() or "n" not in r else j
            if not 0 <= k < len(imgs):
                continue
            h, w = imgs[k][0].shape[:2]
            if (r.get("kind") or "photo") != "photo":
                out[k]["kind"] = r["kind"]
            for o in r.get("overlays") or []:
                b = o.get("box") if isinstance(o, dict) else None
                if not (isinstance(b, list) and len(b) == 4):
                    continue
                y0, x0, y1, x1 = [max(0.0, min(1000.0, float(v))) for v in b]
                if y1 <= y0 or x1 <= x0:
                    continue
                out[k]["boxes"].append([int(x0 / 1000 * w), int(y0 / 1000 * h), int(x1 / 1000 * w) + 1,
                                        int(y1 / 1000 * h) + 1])
                out[k]["what"].append(str(o.get("what", ""))[:80])
    if seen == 0:
        raise Reject("ИИ не посмотрел картинку")
    return out


def look(db, img: np.ndarray, note: str = "") -> dict:
    return look_many(db, [(img, note)])[0]


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001 — one model failing is reported, the other still counts
        return e


def average(fr: list[np.ndarray]) -> np.ndarray:
    acc = np.zeros(fr[0].shape, np.float32)
    step = max(1, len(fr) // 40)
    for f in fr[::step]:
        acc += f
    avg = (acc / len(fr[::step])).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return cv2.cvtColor(clahe.apply(cv2.cvtColor(avg, cv2.COLOR_BGR2GRAY)), cv2.COLOR_GRAY2BGR)


def frame_at(path: Path, t: float) -> np.ndarray | None:
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
    ok, f = cap.read()
    cap.release()
    return f if ok else None


def faint_mask(path: Path, dur: float, fps: float = 6.0, window: float = 2.0) -> np.ndarray | None:
    """Faint fixed lettering: per pixel, the minimum brightness over each 2-second window keeps a fixed
    semi-transparent overlay and drops the moving picture; its high frequencies in most windows are the mask."""
    cap = cv2.VideoCapture(str(path))
    native = cap.get(cv2.CAP_PROP_FPS) or 25
    step = max(1, int(round(native / fps)))
    mins, cur, t0, i = [], None, 0.0, 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if i % step == 0:
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            if max(g.shape) > 960:
                k = 960 / max(g.shape)
                g = cv2.resize(g, (int(g.shape[1] * k), int(g.shape[0] * k)))
            t = i / native
            cur = g if cur is None else np.minimum(cur, g)
            if t - t0 >= window:
                mins.append(cur)
                cur, t0 = None, t
        i += 1
    cap.release()
    if len(mins) < 3:
        return None
    votes = None
    for m in mins:
        hf = np.abs(cv2.Laplacian(cv2.GaussianBlur(m, (3, 3), 0).astype(np.float32), cv2.CV_32F))
        v = (hf > 18).astype(np.uint8)
        votes = v if votes is None else votes + v
    keep = (votes >= 0.8 * len(mins)).astype(np.uint8) * 255
    keep = cv2.morphologyEx(keep, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return keep


def mask_boxes(mask: np.ndarray, min_area: int = 60) -> list[list[int]]:
    n, _, st, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8))
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a >= min_area:
            out.append([int(x), int(y), int(x + w), int(y + h)])
    return out


def merge_boxes(boxes: list[list[int]], pad: int = 6) -> list[list[int]]:
    """Union of overlapping (padded) boxes."""
    bs = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        out: list[list[int]] = []
        for b in bs:
            for o in out:
                if b[0] - pad <= o[2] and o[0] - pad <= b[2] and b[1] - pad <= o[3] and o[1] - pad <= b[3]:
                    o[:] = [min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])]
                    changed = True
                    break
            else:
                out.append(list(b))
        bs = out
    return bs


def crop_plan(boxes: list[list[int]], w: int, h: int, max_share: float, min_keep: float = 0.7,
              margin: int = 6) -> tuple[int, int, int, int] | None:
    """(x, y, cw, ch) that cuts every box off through the nearest edge band, or None when a box sits inside the
    picture or the crop would keep less than min_keep of a side (pure, tested)."""
    cut = {"l": 0, "r": 0, "t": 0, "b": 0}
    for x0, y0, x1, y1 in boxes:
        options = []
        if y1 <= max_share * h:
            options.append(("t", y1 + margin))
        if y0 >= (1 - max_share) * h:
            options.append(("b", h - y0 + margin))
        if x1 <= max_share * w:
            options.append(("l", x1 + margin))
        if x0 >= (1 - max_share) * w:
            options.append(("r", w - x0 + margin))
        if not options:
            return None
        side, amount = min(options, key=lambda o: o[1])
        cut[side] = max(cut[side], amount)
    cw, chh = w - cut["l"] - cut["r"], h - cut["t"] - cut["b"]
    if cw < min_keep * w or chh < min_keep * h:
        return None
    cw, chh = cw - cw % 2, chh - chh % 2
    return cut["l"], cut["t"], cw, chh


# -- the plan for one file -------------------------------------------------------------------------------------------
def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    info = json.loads(out.stdout or "{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    a = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return {"w": int(v.get("width") or 0), "h": int(v.get("height") or 0),
            "dur": float((info.get("format") or {}).get("duration") or 0), "audio": a}


def analyze(db, path: Path) -> dict:
    """What is on the file and what to do: {"video", "w", "h", "dur", "trim", "boxes", "mask", "how", "seen"}."""
    video = path.suffix.lower() in VIDEO
    plan: dict[str, Any] = {"src": str(path), "video": video, "how": [], "seen": []}
    if not video:
        img = cv2.imread(str(path))
        if img is None:
            raise Reject("картинка не читается")
        h, w = img.shape[:2]
        if min(h, w) < 300:
            raise Reject(f"слишком маленькая картинка {w}×{h}")
        r = look(db, img)
        if r["kind"] != "photo":
            raise Reject(f"не фото, а {r['kind']}")
        plan.update(w=w, h=h, dur=0.0, trim=(0.0, 0.0), boxes=merge_boxes(r["boxes"]), mask=None)
        plan["seen"] = r["what"]
        plan["how"].append(f"ИИ: {len(r['boxes'])} знаков" if r["boxes"] else "ИИ: чисто")
        return plan
    info = probe(path)
    w, h, dur = info["w"], info["h"], info["dur"]
    if not w or dur <= 0.5:
        raise Reject("видео не читается")
    if dur > float(config.setting(db, "video_max_s")):
        raise Reject(f"видео {dur:.0f} с — длиннее {config.setting(db, 'video_max_s')} с")
    start, end = 0.0, dur
    fr = cf.frames(path)
    if not fr:
        raise Reject("кадры не читаются")
    avg = average(fr)
    shots = [(frame_at(path, 0.3), "The first frame."), (frame_at(path, dur - 0.4), "The last frame.")]
    shots += [(frame_at(path, dur * sh), f"A frame at {int(sh * 100)}% of the clip.") for sh in (0.15, 0.5, 0.85)]
    shots = [(f, n) for f, n in shots if f is not None] + [(avg, AVG_NOTE)]
    seen_all = look_many(db, shots)
    boxes: list[list[int]] = []
    seen: list[str] = []
    for (f, note), r in zip(shots, seen_all):
        if note.startswith("The first") and r["kind"] != "photo":
            start = min(dur - 1.0, 2.0)  # a branded intro card (Mash, Baza): cut it off
            plan["how"].append("обрезана заставка в начале")
            continue
        if note.startswith("The last") and r["kind"] != "photo":
            end = max(start + 1.0, dur - 2.5)  # a branded outro card
            plan["how"].append("обрезана заставка в конце")
            continue
        if r["kind"] == "screenshot":
            raise Reject("это запись экрана, а не съёмка")
        boxes += r["boxes"]
        seen += r["what"]
    if end - start < 2.0:
        raise Reject("от видео ничего не осталось после заставок")
    if start > 0 or end < dur:  # the new edges must be clean too
        edges = [(frame_at(path, start + 0.2), "The first frame."), (frame_at(path, end - 0.3), "The last frame.")]
        edges = [e for e in edges if e[0] is not None]
        for r in look_many(db, edges) if edges else []:
            if r["kind"] != "photo":
                raise Reject("заставка длиннее, чем ожидалось")
            boxes += r["boxes"]
            seen += r["what"]
    mask = np.zeros((h, w), np.uint8)
    auto, moving, blind = cf.auto_mask(fr)
    if moving >= cf.STILL_SHIFT and not blind:
        if auto.shape != mask.shape:
            auto = cv2.resize(auto, (w, h), interpolation=cv2.INTER_NEAREST)
        mask = np.maximum(mask, auto)
        fm = faint_mask(path, dur)
        if fm is not None:
            mask = np.maximum(mask, cv2.resize(fm, (w, h), interpolation=cv2.INTER_NEAREST))
        plan["how"].append(f"камера движется {moving:.1f} px/кадр: автомаска {float((auto > 0).mean()):.1%}")
    elif moving >= cf.STILL_SHIFT:  # a slow camera keeps the background steady too: the steady-edge mask is blind
        plan["how"].append(f"медленная камера ({moving:.1f} px): автомаска слепа, только ИИ и образцы логотипов")
    else:
        plan["how"].append("неподвижная камера: только ИИ и образцы логотипов")
    known = cf.template_mask(fr)
    if known.shape != mask.shape:
        known = cv2.resize(known, (w, h), interpolation=cv2.INTER_NEAREST)
    if np.any(known):
        plan["how"].append("известный логотип по образцу")
    mask = np.maximum(mask, known)
    for b in boxes:
        mask[b[1]:b[3], b[0]:b[2]] = 255
    plan.update(w=w, h=h, dur=dur, trim=(start, end), boxes=merge_boxes(boxes + mask_boxes(mask)), mask=mask,
                audio=info["audio"], seen=seen)
    plan["how"].append(f"ИИ: {len(seen)} знаков" if seen else "ИИ: чисто")
    return plan


# -- cleaning -------------------------------------------------------------------------------------------------------
def news_busy() -> bool:
    """A news film is being made (films come first on the cleaner)."""
    if subprocess.run(["pgrep", "-f", NEWS_BUSY], capture_output=True, text=True).stdout.strip():
        return True
    out = subprocess.run(["systemctl", "list-units", "--state=active", "--no-legend", "--plain", "news-digest-*",
                          "news-morning-*", "news-produce*", "news-build*"], capture_output=True, text=True).stdout
    return any(".service" in line for line in out.splitlines())


def cleaner_free(cleaner: cf.Cleaner) -> bool:
    try:
        jobs = json.loads(cleaner.call("/api/jobs"))
        jobs = jobs.get("jobs", jobs) if isinstance(jobs, dict) else jobs
        return sum(1 for j in jobs if j.get("status") in ("queued", "running")) <= 1
    except Exception:  # noqa: BLE001
        return False


def ff(args: list[str], timeout: int = 900) -> None:
    subprocess.run(["nice", "-n", "10", "ffmpeg", "-v", "error", "-y", "-threads", "2", *args], check=True,
                   timeout=timeout, capture_output=True)


def do_crop(src: Path, plan: dict, box: tuple[int, int, int, int], dst: Path) -> Path:
    x, y, cw, chh = box
    if not plan["video"]:
        img = cv2.imread(str(src))
        out = dst.with_suffix(".jpg")
        cv2.imwrite(str(out), img[y:y + chh, x:x + cw], [cv2.IMWRITE_JPEG_QUALITY, 92])
        return out
    s, e = plan["trim"]
    out = dst.with_suffix(".mp4")
    ff(["-ss", f"{s:.3f}", "-i", str(src), "-t", f"{e - s:.3f}", "-vf",
        f"crop={cw}:{chh}:{x}:{y},scale='min(iw,1920)':'min(ih,1920)':force_original_aspect_ratio=decrease,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(out)])
    return out


def do_trim(src: Path, plan: dict, dst: Path) -> Path:
    s, e = plan["trim"]
    out = dst.with_suffix(".mp4")
    if s <= 0.05 and e >= plan["dur"] - 0.05:
        ff(["-i", str(src), "-vf", "scale='min(iw,1920)':'min(ih,1920)':force_original_aspect_ratio=decrease,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "21", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)])
    else:
        ff(["-ss", f"{s:.3f}", "-i", str(src), "-t", f"{e - s:.3f}", "-vf",
            "scale='min(iw,1920)':'min(ih,1920)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,"
            "setsar=1,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac",
            "-b:a", "128k", "-movflags", "+faststart", str(out)])
    return out


def do_inpaint(db, src: Path, plan: dict, dst: Path, wait_s: float = 3600) -> Path:
    """The watermark-cleaner (MI-GAN) over the marks; the original sound is put back."""
    cleaner = cf.Cleaner()
    work = dst.parent
    clip = work / (dst.stem + ".in.mp4")
    s, e = plan["trim"]
    cf.prepare(src, s, e - s if plan["video"] else 0.0, clip)
    mask = np.zeros((plan["h"], plan["w"]), np.uint8)
    if plan.get("mask") is not None:
        mask = np.maximum(mask, plan["mask"])
    for b in plan["boxes"]:
        mask[max(0, b[1] - 4):b[3] + 4, max(0, b[0] - 4):b[2] + 4] = 255
    mask = cv2.dilate(mask, np.ones((7, 7), np.uint8))
    jid = cleaner.submit(clip, mask)
    t0 = time.time()
    try:
        while True:
            j = cleaner.status(jid)
            if j["status"] == "succeeded":
                break
            if j["status"] not in ("queued", "running"):
                raise Reject(f"очистка не удалась: {j['status']} {j.get('error') or ''}"[:200])
            if plan["video"] and news_busy():
                cleaner.cancel(jid)
                raise Reject("на сервере собирается новостной ролик — очистка видео уступила ему")
            if time.time() - t0 > wait_s:
                cleaner.cancel(jid)
                raise Reject("очистка слишком долгая")
            time.sleep(8)
        raw = work / (dst.stem + ".clean.mp4")
        cleaner.download(jid, raw)
    finally:
        clip.unlink(missing_ok=True)
    if not plan["video"]:
        out = dst.with_suffix(".jpg")
        ff(["-i", str(raw), "-frames:v", "1", "-q:v", "2", str(out)])
        raw.unlink(missing_ok=True)
        return out
    out = dst.with_suffix(".mp4")
    if plan.get("audio"):
        ff(["-i", str(raw), "-ss", f"{s:.3f}", "-i", str(src), "-t", f"{e - s:.3f}", "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(out)])
        raw.unlink(missing_ok=True)
    else:
        shutil.move(str(raw), str(out))
    return out


def mask_share(plan: dict) -> float:
    """Share of the frame the cleaner would have to repaint (union of the mask and the boxes)."""
    m = np.zeros((plan["h"], plan["w"]), np.uint8) if plan.get("mask") is None else (plan["mask"] > 0).astype(np.uint8)
    for b in plan["boxes"]:
        m[max(0, b[1]):b[3], max(0, b[0]):b[2]] = 1
    return float(m.mean())


def verify(db, out: Path) -> list[str]:
    """What the eyes still see on a result ([] = clean): one look at its frames and their average."""
    if out.suffix.lower() in VIDEO:
        d = probe(out)["dur"]
        shots = [(frame_at(out, d * sh), "") for sh in (0.1, 0.5, 0.9)]
        fr = cf.frames(out, 60)
        if fr:
            shots.append((average(fr), AVG_NOTE))
    else:
        shots = [(cv2.imread(str(out)), "")]
    shots = [(f, n) for f, n in shots if f is not None]
    left: list[str] = []
    for r in look_many(db, shots):
        if r["kind"] == "screenshot":
            left.append("скриншот")
        left += r["what"]
    return left


def normalize(src: Path, dst: Path) -> Path:
    """A working copy small enough for the shared server: a video at most 1280 px on the long side, 30 fps, AAC;
    a photo at most 2048 px. 4K phone clips ran the cleaner and the server out of memory (2026-11-01)."""
    # an explicit name: dst «x.norm».with_suffix(".mp4") was «x.mp4» — the final output's own name (Baza clips 01.11)
    if src.suffix.lower() in VIDEO:
        out = dst.parent / (dst.name.replace(".", "_") + "_work.mp4")
        ff(["-i", str(src), "-vf", "scale='min(iw,1280)':'min(ih,1280)':force_original_aspect_ratio=decrease,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1,fps=30,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(out)])
        return out
    img = cv2.imread(str(src))
    if img is None:
        raise Reject("картинка не читается")
    h, w = img.shape[:2]
    k = min(1.0, 2048 / max(h, w))
    if k < 1:
        img = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
    out = dst.parent / (dst.name.replace(".", "_") + "_work.jpg")
    cv2.imwrite(str(out), img, [cv2.IMWRITE_JPEG_QUALITY, 93])
    return out


def make_clean(db, src: Path, dst: Path, urgent: bool = False, allow_slow: bool = True) -> dict:
    """One file → {"path", "kind", "how"} or raises Reject. dst is the output path without extension."""
    try:
        src = normalize(src, dst.with_name(dst.name + ".norm"))
    except subprocess.CalledProcessError as e:
        raise Reject(f"файл не читается ({e.returncode})") from None
    plan = analyze(db, src)
    how = list(plan["how"])
    if not plan["boxes"]:
        trimmed = plan["video"] and (plan["trim"][0] > 0.05 or plan["trim"][1] < plan["dur"] - 0.05)
        out = do_trim(src, plan, dst) if trimmed else dst.with_suffix(src.suffix.lower())
        if not trimmed:
            shutil.move(str(src), str(out))
        else:
            src.unlink(missing_ok=True)
        return {"path": str(out), "kind": "video" if plan["video"] else "photo", "how": "; ".join(how + ["чисто"])}
    box = crop_plan(plan["boxes"], plan["w"], plan["h"], float(config.setting(db, "crop_max_share")))
    if box:
        out = do_crop(src, plan, box, dst)
        how.append(f"обрезка {plan['w']}×{plan['h']}→{box[2]}×{box[3]}")
    else:
        limit = 0.06 if plan["video"] else 0.10  # more than that and the cleaner leaves a smear
        if mask_share(plan) > limit:
            raise Reject(f"знаки занимают {mask_share(plan):.0%} кадра — чисто не стереть")
        if plan["video"] and not allow_slow:
            raise Slow("видео нужно чистить сервисом — это долго")
        if plan["video"]:
            lim = float(config.setting(db, "inpaint_video_max_s"))
            if plan["trim"][1] - plan["trim"][0] > lim:
                raise Reject(f"знак в кадре, а видео длиннее {lim:g} с для очистки")
            if news_busy():
                raise Reject("знак в кадре, а очистка занята новостным роликом")
        out = do_inpaint(db, src, plan, dst, wait_s=900 if urgent else 3600)
        how.append(f"стёрто сервисом очистки ({mask_share(plan):.1%} кадра)")
    if src != out:
        src.unlink(missing_ok=True)  # the working copy
    left = verify(db, out)
    if left:
        out.unlink(missing_ok=True)
        raise Reject("после очистки остались знаки: " + "; ".join(left[:3]))
    return {"path": str(out), "kind": "video" if plan["video"] else "photo", "how": "; ".join(how)}


def still_of(src: Path, dst: Path, share: float = 0.4) -> Path:
    """A frame of a video as a photo (the urgent post's picture until the video is clean)."""
    d = probe(src)["dur"]
    out = dst.with_suffix(".jpg")
    ff(["-ss", f"{d * share:.2f}", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(out)])
    return out


def file_key(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()[:16]


def cached(db, src: Path) -> dict | None:
    """A file cleaned before (the same video in several channels' posts)."""
    r = db.setting(f"media:{file_key(src)}")
    if r and (r.get("reject") or Path(r.get("path", "")).is_file()):
        return r
    return None


def remember(db, src: Path, result: dict) -> None:
    db.set_setting(f"media:{file_key(src)}", result)


def clean_cached(db, src: Path, dst: Path, urgent: bool = False, allow_slow: bool = True) -> dict:
    hit = cached(db, src)
    if hit:
        if hit.get("reject"):
            raise Reject(hit["reject"])
        return hit
    try:
        res = make_clean(db, src, dst, urgent, allow_slow)
    except Slow:
        raise
    except Reject as e:
        if "уступила" not in str(e) and "занята" not in str(e) and "не посмотрел" not in str(e):
            remember(db, src, {"reject": str(e)})
        raise
    remember(db, src, res)
    return res


def env_ok() -> bool:
    return bool(os.environ.get("APP_USERNAME") and os.environ.get("APP_PASSWORD"))
