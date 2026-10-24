"""Flow illustrations for a news story (Nano Banana Pro, falls back to Nano Banana 2; free account, no video credits).

    .venv/bin/python addons/news/gen_shots.py projects/<slug>/story.json projects/<slug>/illustrations [ids...]

story.json: "illustration_style" (one paragraph, prepended to every prompt) and "illustrations":
[{"id": "i1", "prompt": "..."}]. One 16:9 picture per id -> <out>/<id>.png. Existing files are skipped, so a redraw
is: delete the file, run again with that id. Exit code 3 = daily quota on both models (see addons/mural/gen_panels.py).
Run it on the server (the Flow driver lives there) in the background; it prints DONE at the end.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from addons.mural import gen_panels as g  # noqa: E402

NO_TEXT = "No text, no letters, no signs with writing anywhere unless quoted above."


def main() -> None:
    story = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    g._load_state(out)
    only = set(sys.argv[3:])
    style = story["illustration_style"]
    for s in story["illustrations"]:
        if only and s["id"] not in only:
            continue
        g.draw(out, s["id"], f"{style} {s['prompt']} {NO_TEXT} {g.SINGLE} {g.FRAME['16:9']}", [], "16:9", 1)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
