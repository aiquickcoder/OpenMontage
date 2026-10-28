"""Mural video: chosen panels -> clean + upscale -> strip/grid canvas -> HyperFrames camera tour -> render -> QA.

Runs locally (Mac). Reads projects/<slug>/story.json and projects/<slug>/assets/panels_src/<panel id>.png
(the chosen Flow variant per panel). Writes projects/<slug>/{assets/, hyperframes/, renders/final.mp4, qa/}.

    .venv/bin/python addons/mural/build.py projects/<slug>            # everything
    .venv/bin/python addons/mural/build.py projects/<slug> --skip-upscale --no-render   # re-cut camera only

Rules this encodes (see addons/mural/README.md): one continuous camera (no cuts), camera never leaves the canvas
and never zooms out past it, strip layout travels left->right only, Flow sparkle removed, captions are timed clips.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MUSIC_DIRS = [Path("/opt/openmontage/music"), Path.home() / "Desktop" / "pixabay_примеры"]
W, H, FPS = 1080, 1920, 30
UPSCALE_TIMEOUT_S = 900  # Real-ESRGAN on a Mac under memory pressure can hang; fall back to Lanczos


# -- assets ----------------------------------------------------------------------------------------
def remove_sparkle(img: np.ndarray) -> np.ndarray:
    """Flow/Gemini stamps a small four-point sparkle ~9.4% of the short side in from the bottom-right corner."""
    h, w = img.shape[:2]
    s = min(h, w)
    centre = (int(w - 0.094 * s), int(h - 0.094 * s))
    mask = np.zeros((h, w), np.uint8)
    cv2.circle(mask, centre, int(0.036 * s), 255, -1)
    return cv2.inpaint(img, mask, 9, cv2.INPAINT_TELEA)


def upscale(src: Path, dst: Path, esrgan: bool = True) -> str:
    """2x with OpenMontage's `upscale` tool (Real-ESRGAN anime model); Lanczos + light sharpen if it fails or hangs."""
    if not esrgan:
        return _lanczos(src, dst)
    code = ("from tools.enhancement.upscale import Upscale;import sys;"
            "r=Upscale().execute({'input_path':sys.argv[1],'output_path':sys.argv[2],'scale':2,"
            "'model':'RealESRGAN_x4plus_anime_6B'});sys.exit(0 if r.success else 1)")
    try:
        p = subprocess.run([sys.executable, "-c", code, str(src), str(dst)], cwd=REPO, capture_output=True,
                           timeout=UPSCALE_TIMEOUT_S)
        if p.returncode == 0 and dst.is_file():
            return "esrgan"
    except subprocess.TimeoutExpired:
        pass
    return _lanczos(src, dst)


def _lanczos(src: Path, dst: Path) -> str:
    im = cv2.imread(str(src))
    up = cv2.resize(im, (im.shape[1] * 2, im.shape[0] * 2), interpolation=cv2.INTER_LANCZOS4)
    cv2.imwrite(str(dst), cv2.addWeighted(up, 1.35, cv2.GaussianBlur(up, (0, 0), 1.2), -0.35, 0))
    return "lanczos"


def prepare(proj: Path, story: dict, skip_upscale: bool, esrgan: bool = True) -> dict:
    A = proj / "assets"
    (A / "panels_2x").mkdir(parents=True, exist_ok=True)
    panels, how = [], {}
    for p in story["panels"]:
        src, up = A / "panels_src" / f"{p['id']}.png", A / "panels_2x" / f"{p['id']}.png"
        if not src.is_file():
            sys.exit(f"missing chosen panel {src}")
        if not (skip_upscale and up.is_file()):
            how[p["id"]] = upscale(src, up, esrgan)
            print(f"upscale {p['id']}: {how[p['id']]}", flush=True)
        panels.append(remove_sparkle(cv2.imread(str(up))))
    ph, pw = panels[0].shape[:2]
    panels = [cv2.resize(x, (pw, ph), interpolation=cv2.INTER_AREA) if x.shape[:2] != (ph, pw) else x for x in panels]
    if story.get("layout", "strip") == "grid":
        cols = 2
        rows = [np.hstack(panels[i:i + cols]) for i in range(0, len(panels), cols)]
        canvas, grid = np.vstack(rows), (cols, len(rows))
    else:
        canvas, grid = np.hstack(panels), (len(panels), 1)
    cv2.imwrite(str(A / "canvas.png"), canvas)
    meta = {"panel_w": pw, "panel_h": ph, "cols": grid[0], "rows": grid[1], "upscale": how}
    (A / "canvas.json").write_text(json.dumps(meta))
    return meta


# -- composition -----------------------------------------------------------------------------------
MAX_CUE_WORDS, CUE_PUNCT = 4, (".", ",", "!", "?", "…", ":", ";", "—")


def word_cues(words: list) -> list[tuple[float, float, str]]:
    """Captions from real word timings: 2–4 words, split after punctuation, on screen while spoken."""
    cues, cur = [], []
    for w in words:
        if not cur and cues and not any(ch.isalnum() for ch in w[0]):  # a lone «—» rides with the cue before it
            cues[-1].append(w)
            continue
        cur.append(w)
        if len(cur) >= MAX_CUE_WORDS or w[0].rstrip().endswith(CUE_PUNCT):
            cues.append(cur)
            cur = []
    if cur:
        if cues and len(cur) == 1 and len(cues[-1]) < MAX_CUE_WORDS + 1:
            cues[-1] += cur
        else:
            cues.append(cur)
    out = []
    for c in cues:
        a, b = c[0][1], max(c[-1][2] + 0.15, c[0][1] + 0.6)
        out.append((round(a, 2), round(b, 2), " ".join(w[0] for w in c)))
    for k in range(len(out) - 1):  # never overlap the next cue
        if out[k][1] > out[k + 1][0]:
            out[k] = (out[k][0], out[k + 1][0], out[k][2])
    # a zero-length cue would stay on screen to the end of the film (HyperFrames reads no duration as «all of it»)
    return [c for c in out if c[1] - c[0] >= 0.05]


def panel_timing(proj: Path, story: dict) -> Optional[dict]:
    path = proj / "assets" / "timing.json"
    return json.loads(path.read_text(encoding="utf-8")) if story.get("narration") and path.is_file() else None


def camera_keys(story: dict, meta: dict, timing: Optional[dict] = None) -> tuple[list, list, list, float]:
    """Camera keys, caption cues and shake windows on the global clock.

    Without narration a panel lasts `duration` s and camera/caption times are seconds from its start. With
    narration (assets/timing.json) a panel lasts as long as its beat is spoken, camera/shake times are FRACTIONS
    (0..1) of that span, and captions come from the real word timings."""
    pw, ph, cols = meta["panel_w"], meta["panel_h"], meta["cols"]
    fit = H / ph if story.get("layout", "strip") != "grid" else max(W / pw, H / (ph * meta["rows"]))
    spans = {p["id"]: p for p in (timing or {}).get("panels", [])}
    keys, caps, shakes, start = [], [], [], 0.0
    for i, p in enumerate(story["panels"]):
        r, c = divmod(i, cols)
        span = spans.get(p["id"])
        if span:
            start, dur = span["start"], span["end"] - span["start"]
            at = lambda t: start + t * dur  # noqa: E731
        else:
            dur = p["duration"]
            at = lambda t: start + t  # noqa: E731
        for k in p["camera"]:
            t, fx, fy, zoom, ease = k[:5]
            rot = k[5] if len(k) > 5 else 0
            keys.append((round(at(t), 3), (c * pw + fx * pw, r * ph + fy * ph), fit * zoom, rot, ease))
        if span and not p.get("captions"):
            caps += word_cues(span["words"])
        else:
            for a, b, text in p.get("captions", []):
                caps.append((round(start + a, 2), round(start + b, 2), text))
        if p.get("shake"):
            a, b, amp = p["shake"]
            shakes.append((round(at(a), 2), round(at(b), 2), amp))
        start += dur
    keys.sort(key=lambda k: k[0])
    return keys, caps, shakes, round(start, 3)


def find_music(src: str) -> Optional[Path]:
    p = Path(src).expanduser()
    if p.is_file():
        return p
    for d in MUSIC_DIRS:
        if (d / p.name).is_file():
            return d / p.name
    return None


def write_workspace(proj: Path, story: dict, meta: dict) -> Path:
    ws = proj / "hyperframes"
    (ws / "assets").mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(ws / "assets" / "canvas.jpg"), cv2.imread(str(proj / "assets" / "canvas.png")),
                [cv2.IMWRITE_JPEG_QUALITY, 93])
    shutil.copy2(HERE / "vendor" / "gsap.min.js", ws / "assets" / "gsap.min.js")  # offline-safe on the server
    music = dict(story.get("music") or {})
    if music.get("src"):
        found = find_music(music["src"])
        if found is None:
            print(f"music {music['src']} not found — rendering without music", flush=True)
            music = {}
        else:
            shutil.copy2(found, ws / "assets" / "music.mp3")
    timing = panel_timing(proj, story)
    narration = timing is not None and (proj / "assets" / "narration.mp3").is_file()
    if narration:
        shutil.copy2(proj / "assets" / "narration.mp3", ws / "assets" / "narration.mp3")
    keys, caps, shakes, duration = camera_keys(story, meta, timing)
    MW, MH = meta["panel_w"] * meta["cols"], meta["panel_h"] * meta["rows"]
    min_scale = max((H + 60) / MH, (W + 60) / MW)

    def state(k):
        _, (cx, cy), s, rot, _ = k
        return {"cx": round(cx, 1), "cy": round(cy, 1), "scale": round(s, 4), "rot": rot}

    tweens = [f"tl.to(cam, {{...{json.dumps(state(b))}, duration: {b[0] - a[0]:.3f}, ease: {json.dumps(b[4] or 'none')}, "
              f"onUpdate: applyCamera }}, {a[0]:.3f});" for a, b in zip(keys, keys[1:])]
    shake_js = "\n".join(
        f"  tl.to(shake, {{ a: {amp}, duration: 0.25, ease: 'power2.out', onUpdate: applyCamera }}, {a});\n"
        f"  tl.to(shake, {{ a: 0, duration: {b - a - 0.25:.2f}, ease: 'power2.in', onUpdate: applyCamera }}, {a + 0.25:.2f});"
        for a, b, amp in shakes)
    caps_html = "\n".join(
        f'    <div id="cap{i}" class="clip cap" data-start="{a}" data-duration="{b - a:.2f}" data-track-index="5">'
        f'<span>{t}</span></div>' for i, (a, b, t) in enumerate(caps))
    caps_js = "\n".join(
        f'  tl.from("#cap{i} span", {{opacity: 0, y: 18, duration: 0.18, ease: "power2.out"}}, {a});\n'
        f'  tl.to("#cap{i} span", {{opacity: 0, duration: 0.12, ease: "power1.in"}}, {b - 0.12:.2f});'
        for i, (a, b, _) in enumerate(caps))
    # music sits ~-18 dB under a narrator (volume 0.14), ~-6 dB without one (0.5)
    music_vol = music.get("volume", 0.14 if narration else 0.5)
    audio = (f'    <audio id="bgm" src="assets/music.mp3" data-start="0" data-duration="{duration}" '
             f'data-media-start="{music.get("offset", 0)}" data-track-index="10" data-volume="{music_vol}"></audio>'
             if music.get("src") else "")
    if narration:
        audio += (f'\n    <audio id="voice" src="assets/narration.mp3" data-start="0" data-track-index="11" '
                  f'data-volume="1"></audio>')
    audio_js = f'  tl.to("#bgm", {{ volume: 0, duration: 2.0, ease: "power1.in" }}, {duration - 2.0});' if audio else ""
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width={W}, height={H}" />
  <title>{story.get('title', story['slug'])}</title>
  <script src="assets/gsap.min.js"></script>
  <style>
    body {{ margin: 0; background: #120c08; }}
    #root {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; background: #120c08; }}
    #stage {{ position: absolute; inset: 0; overflow: hidden; }}
    #world {{ position: absolute; left: 0; top: 0; width: {MW}px; height: {MH}px; transform-origin: 0 0; will-change: transform; }}
    #world img {{ display: block; width: {MW}px; height: {MH}px; }}
    .vignette {{ position: absolute; inset: 0; pointer-events: none;
                 background: radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.45) 100%); }}
    .cap {{ position: absolute; left: 60px; right: 60px; bottom: 360px; text-align: center; }}
    .cap span {{ display: inline-block; font-family: "Arial Black", "DejaVu Sans", Arial, sans-serif; font-weight: 900; font-size: 68px;
                 line-height: 1.15; color: #fff; text-shadow: 0 0 6px rgba(0,0,0,.85), 0 4px 14px rgba(0,0,0,.7);
                 -webkit-text-stroke: 2px rgba(0,0,0,.55); paint-order: stroke fill; }}
    #fade {{ position: absolute; inset: 0; background: #000; opacity: 0; pointer-events: none; }}
  </style>
</head>
<body>
  <div id="root" data-composition-id="main" data-width="{W}" data-height="{H}" data-duration="{duration}">
    <section id="stage" class="clip" data-start="0" data-duration="{duration}" data-track-index="1">
      <div id="world" data-layout-allow-overflow><img src="assets/canvas.jpg" alt="" /></div>
      <div class="vignette"></div>
    </section>
{caps_html}
    <div id="fade" class="clip" data-start="0" data-duration="{duration}" data-track-index="8"></div>
{audio}
  </div>
<script>
  window.__timelines = window.__timelines || {{}};
  const tl = gsap.timeline({{ paused: true }});
  const world = document.getElementById("world");
  // viewport-change (single wrapper, origin 0 0) + multi-phase-camera micro-drift / shake window
  const cam = {json.dumps(state(keys[0]))};
  const drift = {{ p: 0 }}, shake = {{ a: 0 }};
  function applyCamera() {{
    const t = drift.p * {duration};
    const dx = Math.sin(t * 0.9) * 4 + Math.sin(t * 23.0) * shake.a;
    const dy = Math.sin(t * 0.69) * 2.5 + Math.cos(t * 19.0) * shake.a * 0.8;
    const sc = Math.max(cam.scale, {min_scale:.5f});             // never zoom out past the canvas
    const m = 24 / sc, hw = {W / 2} / sc + m, hh = {H / 2} / sc + m;  // keep the frame inside the canvas
    const cx = hw * 2 >= {MW} ? {MW / 2} : Math.min(Math.max(cam.cx, hw), {MW} - hw);
    const cy = hh * 2 >= {MH} ? {MH / 2} : Math.min(Math.max(cam.cy, hh), {MH} - hh);
    const r = cam.rot * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
    const px = sc * cx, py = sc * cy;
    const x = {W / 2} - (px * c - py * s) + dx, y = {H / 2} - (px * s + py * c) + dy;
    world.style.transform = `translate(${{x}}px, ${{y}}px) rotate(${{cam.rot}}deg) scale(${{sc}})`;
  }}
  applyCamera();
  tl.to(drift, {{ p: 1, duration: {duration}, ease: "none", onUpdate: applyCamera }}, 0);
{chr(10).join('  ' + t for t in tweens)}
{shake_js}
{caps_js}
{audio_js}
  tl.to("#fade", {{ opacity: 1, duration: 0.8, ease: "power1.in" }}, {duration - 0.8});
  window.__timelines["main"] = tl;
</script>
</body>
</html>
"""
    (ws / "index.html").write_text(html, encoding="utf-8")
    (ws / "hyperframes.json").write_text(json.dumps({"name": story["slug"], "fps": FPS}, indent=1))
    (ws / "DESIGN.md").write_text(f"# {story.get('title', story['slug'])}\n\nOne continuous camera over a "
                                  f"{story.get('layout', 'strip')} canvas of {len(story['panels'])} panels "
                                  "(spatial-pan-stations). Built by addons/mural/build.py.\n")
    return ws


# -- render + QA -----------------------------------------------------------------------------------
def render(ws: Path, out: Path) -> None:
    # Same commands as hyperframes_compose.render_existing (check, then render --strict). Called directly because
    # the tool's 5 s `npm view` runtime probe times out when the registry answers slowly (~12 s from RU).
    out.parent.mkdir(parents=True, exist_ok=True)
    hf = os.environ.get("HYPERFRAMES_BIN", "npx --yes hyperframes").split()  # server: a pinned global install
    for args in (["check"], ["render", "--output", str(out.resolve()), "--fps", str(FPS), "--quality", "high", "--strict"]):
        p = subprocess.run([*hf, *args], cwd=ws, capture_output=True, text=True)
        tail = (p.stdout + p.stderr).strip().splitlines()[-4:]
        print(f"hyperframes {args[0]}: exit {p.returncode}\n  " + "\n  ".join(tail), flush=True)
        if p.returncode != 0:
            sys.exit(f"hyperframes {args[0]} failed")


def qa(proj: Path, video: Path, story: dict) -> dict:
    cap = cv2.VideoCapture(str(video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    bands = []
    for i in range(0, max(0, n - 30), 3):  # the last second fades to black on purpose
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        for name, band in (("top", g[:10]), ("bottom", g[-10:]), ("left", g[:, :10]), ("right", g[:, -10:])):
            if band.mean() < 20 and band.std() < 4:
                bands.append((round(i / FPS, 1), name))
    (proj / "qa").mkdir(exist_ok=True)
    frames, start = [], 0.0
    for p in story["panels"]:  # one frame at the end of every panel's stay
        t = start + p["duration"] - 0.3
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, f = cap.read()
        if ok:
            f = cv2.resize(f, (360, 640))
            cv2.putText(f, f"{p['id']} {t:.1f}s", (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            frames.append(f)
        start += p["duration"]
    if frames:
        cv2.imwrite(str(proj / "qa" / "contact.jpg"), np.hstack(frames), [cv2.IMWRITE_JPEG_QUALITY, 90])
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                            "-of", "json", str(video)], capture_output=True, text=True).stdout
    report = {"bands": bands, "probe": json.loads(probe or "{}"), "contact": str(proj / "qa" / "contact.jpg")}
    (proj / "qa" / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--skip-upscale", action="store_true", help="reuse assets/panels_2x when present")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--server", action="store_true", help="factory server: Lanczos upscale (no Real-ESRGAN there)")
    a = ap.parse_args()
    proj = a.project.resolve()
    story = json.loads((proj / "story.json").read_text(encoding="utf-8"))
    meta = prepare(proj, story, a.skip_upscale, esrgan=not a.server)
    ws = write_workspace(proj, story, meta)
    print("workspace", ws, flush=True)
    if a.no_render:
        return
    out = proj / "renders" / "final.mp4"
    render(ws, out)
    report = qa(proj, out, story)
    print(json.dumps({"bands": report["bands"][:10], "n_bands": len(report["bands"]), "contact": report["contact"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
