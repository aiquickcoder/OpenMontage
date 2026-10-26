"""Veo clips of a city's cat anchor from his base frame (free Flow credits: veo-lite = 10; needs the non-RU egress).

    FLOW_PROXY=socks5://127.0.0.1:20901 .venv/bin/python addons/news/veo_anchor.py <anchor_dir> <out.mp4> --intro
    FLOW_PROXY=socks5://127.0.0.1:20901 .venv/bin/python addons/news/veo_anchor.py <anchor_dir> <out.mp4> --line "…"

--intro: no speech — slow push-in, the cat looks up from his papers into the camera (the opener of every film).
--line:  the cat says this exact Russian line in his own Veo voice, lips in sync (the stories stay cedar voice-over).
The first frame is <anchor_dir>/base.png. Prints one JSON line: success, output, credits.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.video.flow_veo_video import FlowVeoVideo  # noqa: E402

KEEP = ("The camera is locked off apart from what is described, no cut. The studio, the monitors and their on-screen "
        "text, the desk with everything on it and the lighting stay exactly as in the first frame. No music, no "
        "captions, no subtitles, no added text.")
INTRO = ("The cartoon cat TV news anchor from the first frame sits at the news desk looking down at his papers. A slow, "
         "smooth camera push-in towards him. After a moment he raises his head and looks straight into the camera "
         "with a calm, attentive face and gives one small nod. His mouth stays closed, he does not speak and does not "
         "smile. Only a quiet studio room tone, no voice. " + KEEP)
LINE = ("The cartoon cat TV news anchor from the first frame sits at the news desk and looks straight into the camera. "
        "He says this exact Russian line clearly, at a natural calm pace, in a warm low male news-anchor voice: «{line}» "
        "His mouth and jaw move in sync with every syllable; when the line ends he closes his mouth and keeps looking "
        "at the viewer. Small natural head movement, one or two blinks. He does not smile broadly and does not stand "
        "up. Only his voice, no other sound. " + KEEP)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("anchor_dir", type=Path)
    ap.add_argument("out", type=Path)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--intro", action="store_true")
    g.add_argument("--line")
    ap.add_argument("--model", default="veo-lite")
    a = ap.parse_args()
    base = a.anchor_dir / "base.png"
    r = FlowVeoVideo().execute({
        "operation": "image_to_video", "model": a.model, "duration": 8, "aspect_ratio": "9:16",
        "image_path": str(base.resolve()), "prompt": INTRO if a.intro else LINE.format(line=a.line),
        "project_dir": str(a.out.parent.resolve()), "output_path": str(a.out.resolve()),
    })
    print(json.dumps({"success": r.success, "error": str(r.error)[:300] if r.error else None,
                      "output": (r.data or {}).get("output_path"), "credits_after": (r.data or {}).get("credits_after"),
                      "project_url": (r.data or {}).get("project_url")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
