"""Looks of a city's cat (owner 2026-10-30: «компромисс» — the same cat, a fresh scene): rain or snow at the window,
the city at night, tea and a newspaper on the desk. The cat stays the channel's face; only what is around him changes.

    .venv/bin/python addons/news/anchor_looks.py draw addons/news/anchor_kzn rain snow night tea   # Flow, on the server
    .venv/bin/python addons/news/anchor_looks.py compose addons/news/anchor_kzn                     # -> layers/looks/

draw: a Flow edit of base.png per look (KEEP + the change; the head is told to stay) -> <dir>/looks/<name>.png.
compose: the edit aligned onto the base (ECC), then the cat's head — the mouth and eyes boxes of anchor.json with
room for the ears and the cheeks — and the monitors with the brand pasted back from the base with a soft edge, so the lip-flap and blink patches
(layers/open|mid|blink.png) fit every look exactly -> <dir>/layers/looks/<name>.jpg. build.py takes a look by the
day (story.json "anchor": {"look": "<name>"} fixes one, "base" the plain studio).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from addons.news import anchor as anc  # noqa: E402

LOOKS = {
    "rain": "it is raining outside: rain streaks and drops on the window glass, the view behind it grey and wet; a "
            "folded dark umbrella leans against the desk",
    "snow": "the first snow is falling outside the window, a thin layer of snow on the window ledge, soft white light",
    "night": "it is night outside: the view in the window is the city lit up at night, and a warm desk lamp lights "
             "the desk",
    "tea": "on the desk there is now a glass of tea in a metal glass holder and a folded newspaper (no readable text "
           "on it)",
    "autumn": "outside the window it is golden autumn: yellow and orange trees, warm low sunlight",
    "morning": "bright morning sun shines through the window, long warm light across the desk",
}
HEAD = "The cat's head, face, eyes, mouth, whiskers and ears stay exactly the same, pixel for pixel."


def head_mask(cfg: dict, pad: tuple[int, int, int, int] = (110, 210, 110, 70)) -> np.ndarray:
    """Where the base is kept: the mouth and eyes boxes together, widened for the ears (up) and the cheeks; soft edge."""
    xs = [cfg[k][i] for k in ("mouth_box", "eyes_box") for i in (0, 2)]
    ys = [cfg[k][i] for k in ("mouth_box", "eyes_box") for i in (1, 3)]
    x0, y0 = max(0, min(xs) - pad[0]), max(0, min(ys) - pad[1])
    x1, y1 = min(anc.W, max(xs) + pad[2]), min(anc.H, max(ys) + pad[3])
    m = np.zeros((anc.H, anc.W), np.float32)
    cv2.ellipse(m, ((x0 + x1) // 2, (y0 + y1) // 2), ((x1 - x0) // 2, (y1 - y0) // 2), 0, 0, 360, 1.0, -1)
    return cv2.GaussianBlur(m, (0, 0), 18)[..., None]


def monitors_mask(cfg: dict) -> np.ndarray:
    """The studio monitors over the cat («ЧП · КАЗАНЬ», «ПРОИСШЕСТВИЯ»): the brand, kept from the base too (a rain look
    lost the right one's text, 2026-10-30). anchor.json "monitors_band": the top rows they sit in (default 380)."""
    m = np.zeros((anc.H, anc.W), np.float32)
    m[: int(cfg.get("monitors_band", 380))] = 1.0
    return cv2.GaussianBlur(m, (0, 0), 14)[..., None]


def compose(d: Path) -> list[str]:
    cfg = json.loads((d / "anchor.json").read_text())
    from addons.mural.build import remove_sparkle

    base = anc.fit(remove_sparkle(cv2.imread(str(d / "base.png"))))
    keep = np.maximum(head_mask(cfg), monitors_mask(cfg))
    out = d / "layers" / "looks"
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for src in sorted((d / "looks").glob("*.png")):  # a look moved aside (<name>.png.hold) is not used
        var = anc.align(base, anc.fit(remove_sparkle(cv2.imread(str(src)))))
        look = (keep * base + (1 - keep) * var).astype(np.uint8)
        changed = float((cv2.absdiff(base, var).max(axis=2) > 24).mean())
        cv2.imwrite(str(out / f"{src.stem}.jpg"), look, [cv2.IMWRITE_JPEG_QUALITY, 93])
        made.append(src.stem)
        print(f"  {src.stem}: {changed:.0%} of the frame changed around the cat", flush=True)
    return made


def draw(d: Path, names: list[str]) -> None:
    from addons.mural import gen_panels as g
    from addons.news.gen_anchor import KEEP

    (d / "looks").mkdir(exist_ok=True)
    g._load_state(d / "looks")
    for n in names:
        g.draw(d / "looks", n, f"{KEEP} Change ONLY this: {LOOKS[n]}. {HEAD} {g.SINGLE}", [str(d / "base.png")],
               "9:16", 1)
    print("DONE", flush=True)


def pick(d: Path, story: dict, day: int) -> Path:
    """The base frame for this film: the story's "look", else one by the day (the plain studio counts as one)."""
    looks = sorted((d / "layers" / "looks").glob("*.jpg")) if (d / "layers" / "looks").is_dir() else []
    want = (story.get("anchor") or {}).get("look")
    if want:
        return d / "layers" / "base.jpg" if want == "base" else d / "layers" / "looks" / f"{want}.jpg"
    choices = [d / "layers" / "base.jpg", *looks]
    return choices[day % len(choices)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["draw", "compose"])
    ap.add_argument("dir", type=Path)
    ap.add_argument("looks", nargs="*", help=f"draw: which ({', '.join(LOOKS)})")
    a = ap.parse_args()
    draw(a.dir, a.looks or list(LOOKS)) if a.what == "draw" else compose(a.dir)


if __name__ == "__main__":
    main()
