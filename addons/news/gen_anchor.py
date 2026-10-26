"""Anchor frames for a city's cat (Flow, Nano Banana Pro -> Nano Banana 2; free account, no video credits).

    .venv/bin/python addons/news/gen_anchor.py <owner_picture.png> addons/news/anchor_<city> \
        --left "ЧП · КАЗАНЬ" --right "ПРОИСШЕСТВИЯ"

1. base.png  = the owner's picture (mouth closed) redrawn with the two studio monitors re-lettered, nothing else changed;
2. open.png, mid.png, blink.png = edits of base.png (mouth open / half open / eyes shut), same prompts as anchor_cat.
Then write <dir>/anchor.json (mouth_box, eyes_box in 1080x1920 frame coordinates) and run anchor.prepare(<dir>), see
addons/news/README.md «Ведущий». Existing files are skipped, so a redraw is: delete it, run again. Prints DONE.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from addons.mural import gen_panels as g  # noqa: E402

KEEP = ("Reproduce the attached image exactly — identical composition, framing, camera, pose, paws, clothes, studio, "
        "monitors, on-screen text, lighting and colours, pixel for pixel.")
EDITS = {
    "open": "Change ONLY this: the cat's mouth is open mid-word as if he is speaking: jaw dropped, a clear dark mouth "
            "opening, tongue and a hint of teeth visible. The eyes and the rest of the face stay exactly the same.",
    "mid": "Change ONLY this: the cat's mouth is slightly open as if saying a soft syllable: a small dark gap between "
           "the lips. The eyes and the rest of the face stay exactly the same.",
    "blink": "Change ONLY this: the cat's eyes are closed (mid-blink, eyelids shut, calm). The mouth and the rest of the "
             "face stay exactly the same.",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("picture", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--left", required=True, help="text for the left studio monitor")
    ap.add_argument("--right", required=True, help="text for the right studio monitor")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    g._load_state(a.out)
    shutil.copy2(a.picture, a.out / "owner.png")
    base = (f"{KEEP.replace(' monitors, on-screen text,', '')} Change ONLY the two monitors hanging above the studio: "
            f"the left monitor shows a bold red banner with the white text «{a.left}»; the right monitor shows the city at "
            f"night with the large white text «{a.right}». Spell the on-screen text exactly as given; nothing else is "
            f"written on the monitors. The cat, his face with the closed mouth, his paws, clothes, the desk with the food, "
            f"the studio and the window stay exactly the same. {g.SINGLE} {g.FRAME['9:16']}")
    g.draw(a.out, "base", base, [str(a.out / "owner.png")], "9:16", 1)
    for name, change in EDITS.items():
        g.draw(a.out, name, f"{KEEP} {change} {g.SINGLE}", [str(a.out / "base.png")], "9:16", 1)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
