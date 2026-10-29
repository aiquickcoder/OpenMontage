"""Cartoon anchor: one base picture + mouth/eye patches cut from Flow edits of it, switched by the voice.

    .venv/bin/python addons/news/anchor.py prepare <anchor dir>     # base.png + open.png (+ mid.png, blink.png) -> layers

<anchor dir>/anchor.json (written by hand once, after looking at base.png):
  {"mouth_box": [x0, y0, x1, y1], "eyes_box": [x0, y0, x1, y1]}      # boxes in the 1080x1920 frame, generous
Each variant (open / mid / blink) is a Flow edit of base.png ("same picture, only the mouth open"). Flow redraws the
whole picture, so the variant is first aligned onto the base (ECC, affine), then only the pixels that changed inside
the box are kept (feathered alpha). The result never moves the head: everything outside the patch is the base.

prepare writes <anchor dir>/layers/{base.jpg, open.png, mid.png, blink.png} at 1080x1920 and layers.json
{"open": [x, y, w, h], ...} (patch position in the 1080x1920 frame).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

W, H = 1080, 1920
BOX_FOR = {"open": "mouth_box", "mid": "mouth_box", "blink": "eyes_box"}


def fit(img: np.ndarray) -> np.ndarray:
    """Cover-crop to 9:16 and resize to 1080x1920."""
    h, w = img.shape[:2]
    s = max(W / w, H / h)
    r = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_LANCZOS4)
    y, x = (r.shape[0] - H) // 2, (r.shape[1] - W) // 2
    return r[y:y + H, x:x + W]


def align(base: np.ndarray, var: np.ndarray) -> np.ndarray:
    g0 = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g1 = cv2.cvtColor(var, cv2.COLOR_BGR2GRAY).astype(np.float32)
    small = 0.25
    a, b = cv2.resize(g0, None, fx=small, fy=small), cv2.resize(g1, None, fx=small, fy=small)
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        _, warp = cv2.findTransformECC(a, b, warp, cv2.MOTION_AFFINE,
                                       (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-5), None, 5)
    except cv2.error:
        print("  ECC did not converge — using the variant unaligned", flush=True)
        return var
    warp[:, 2] /= small
    return cv2.warpAffine(var, warp, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE)


def patch(base: np.ndarray, var: np.ndarray, box: list[int]) -> tuple[np.ndarray, list[int]]:
    x0, y0, x1, y1 = box
    d = cv2.absdiff(base[y0:y1, x0:x1], var[y0:y1, x0:x1]).max(axis=2)
    m = (cv2.GaussianBlur(d, (0, 0), 3) > 18).astype(np.uint8) * 255
    m = cv2.dilate(m, np.ones((15, 15), np.uint8))
    m = cv2.GaussianBlur(m, (0, 0), 6)
    # feather the box edge too, so a change touching the box never ends in a hard line
    edge = np.zeros_like(m)
    cv2.rectangle(edge, (8, 8), (edge.shape[1] - 9, edge.shape[0] - 9), 255, -1)
    m = np.minimum(m, cv2.GaussianBlur(edge, (0, 0), 5))
    rgba = cv2.cvtColor(var[y0:y1, x0:x1], cv2.COLOR_BGR2BGRA)
    rgba[:, :, 3] = m
    return rgba, [x0, y0, x1 - x0, y1 - y0]


def prepare(d: Path) -> dict:
    cfg = json.loads((d / "anchor.json").read_text())
    from addons.mural.build import remove_sparkle

    base = fit(remove_sparkle(cv2.imread(str(d / "base.png"))))  # Flow's corner sparkle
    out = d / "layers"
    out.mkdir(exist_ok=True)
    cv2.imwrite(str(out / "base.jpg"), base, [cv2.IMWRITE_JPEG_QUALITY, 93])
    layers = {}
    for name, key in BOX_FOR.items():
        src = d / f"{name}.png"
        if not src.is_file() or key not in cfg:
            continue
        var = align(base, fit(cv2.imread(str(src))))
        rgba, pos = patch(base, var, cfg[key])
        cv2.imwrite(str(out / f"{name}.png"), rgba)
        layers[name] = pos
        print(f"  {name}: patch {pos}, alpha cover {float((rgba[:, :, 3] > 128).mean()):.0%}", flush=True)
    (out / "layers.json").write_text(json.dumps(layers, indent=1))
    return layers


# -- voice -> mouth -----------------------------------------------------------------------------------
def envelope(audio: Path, fps: int) -> np.ndarray:
    """RMS loudness per video frame (0..1, normalised to the 95th percentile)."""
    sr = 16000
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
    hop = sr // fps
    n = len(x) // hop
    rms = np.sqrt(np.mean(x[:n * hop].reshape(n, hop) ** 2, axis=1))
    ref = np.percentile(rms[rms > 1e-4], 95) if (rms > 1e-4).any() else 1.0
    return np.clip(rms / ref, 0, 1)


def mouth_track(audio: Path, fps: int, windows: list[tuple[float, float]], offset: float,
                has_mid: bool) -> list[tuple[float, str]]:
    """Mouth state changes (time, 'closed'|'mid'|'open') inside the anchor windows. Holds every state at least
    2 frames (cartoon lip-flap reads better on twos) and closes the mouth at the end of each window.
    Loudness is judged against the loudest moment within ±0.2 s as well as against the whole take: a quick narrator
    keeps the level up between syllables, and with whole-take thresholds the mouth hung open (owner 2026-10-29, the
    faster «Энергичный Рассказчик»: 2.4 closings a second at 5.2 syllables; now 3.1, closed 42% of the time)."""
    env = envelope(audio, fps)
    k = max(1, int(0.2 * fps))
    pad = np.pad(env, k, mode="edge")
    rel = env / np.maximum(np.array([pad[i:i + 2 * k + 1].max() for i in range(len(env))]), 0.15)
    out: list[tuple[float, str]] = []
    for a, b in windows:
        cur, held = "closed", 99
        out.append((round(a, 3), "closed"))
        for f in range(int(a * fps), int(b * fps)):
            k = f - int(offset * fps)  # voice starts `offset` seconds into the composition
            v, r = (env[k], rel[k]) if 0 <= k < len(env) else (0.0, 0.0)
            want = "open" if (v > 0.35 and r > 0.75) else ("mid" if (v > 0.12 and r > 0.38) else "closed")
            if want == "mid" and not has_mid:
                want = "open" if (v > 0.25 and r > 0.6) else "closed"
            held += 1
            if want != cur and held >= 2:
                cur, held = want, 0
                out.append((round(f / fps, 3), cur))
        out.append((round(b, 3), "closed"))
    return out


def blink_track(windows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Blinks every ~3.1–4.3 s inside the anchor windows (deterministic), 0.12 s each."""
    out = []
    for a, b in windows:
        t, k = a + 1.1, 0
        while t + 0.15 < b:
            out.append((round(t, 2), 0.12))
            t += 3.1 + (k * 0.7) % 1.2
            k += 1
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "prepare":
        sys.exit(__doc__)
    print(json.dumps(prepare(Path(sys.argv[2])), indent=1))
