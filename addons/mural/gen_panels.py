"""Draw a mural story's cast sheets and panels in Google Flow (Nano Banana Pro, free) through the Flow driver.

Runs on the factory server as user factory (DISPLAY, FLOW_* env from /opt/openmontage/.env):

    systemd-run --unit=factory-mural --uid=factory --slice=factory.slice \
      -p EnvironmentFile=/opt/openmontage/.env -p Environment=DISPLAY=:99 -p Environment=HOME=/home/factory \
      -p Environment=PYTHONPATH=/opt/openmontage -p WorkingDirectory=/opt/openmontage \
      -p StandardOutput=file:/tmp/mural.out -p StandardError=file:/tmp/mural.err \
      /opt/openmontage/.venv/bin/python addons/mural/gen_panels.py <story.json> <out_dir> [panel ids...]

Writes <out_dir>/<sheet id>.png, <panel id>.png (+ _2 variant) and .prompt.txt. Existing files are skipped, so a
re-run resumes; pass panel ids to redraw only those (delete their old files first). Prints DONE at the end.
"""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/openmontage")
from tools.graphics.flow_image import FlowImage  # noqa: E402

# A comic-grid trap: words like "panel" / "in every panel" make Nano Banana draw a 4-up comic inside one image.
SINGLE = "One single continuous image, one moment, one camera angle — not a comic, not a grid, no panels, no split screen."
FRAME = {
    "1:1": "1:1 square composition filling the frame edge to edge, no letterbox, no borders, no text, no watermark.",
    "9:16": "Vertical 9:16 composition filling the frame edge to edge, no letterbox, no borders, no text, no watermark.",
    "16:9": "Wide 16:9 composition filling the frame edge to edge, no letterbox, no borders, no text, no watermark.",
}
FALLBACK_MODEL = "nano2"  # Nano Banana 2: separate per-model daily quota, same driver
# A spent Pro quota often comes back as an empty answer ("without producing media"), not as a QUOTA code.
# After PRO_DOWN_AFTER such empty Pro answers in a row, the rest of the run goes straight to the fallback model.
PRO_DOWN_AFTER = 2
_pro_empty = 0
SHEET = ("Character turnaround sheet: front view, three-quarter view and side profile of the same character side by "
         "side, neutral standing pose, identical proportions and outfit in every view, plain light grey background, "
         "even soft studio lighting. No text, no labels.")


def draw(out: Path, name: str, prompt: str, refs: list[str], aspect: str, variants: int) -> bool:
    """Up to 3 tries: Nano Banana Pro with refs twice, then Nano Banana 2 (its own daily quota) with refs.
    A Pro quota error jumps straight to Nano Banana 2; exit code 3 only when both are out of quota."""
    if (out / f"{name}.png").is_file():
        print(f"{name} exists, skip", flush=True)
        return True
    global _pro_empty
    plan = ["nano-pro", "nano-pro", FALLBACK_MODEL]
    attempt = plan.index(FALLBACK_MODEL) if _pro_empty >= PRO_DOWN_AFTER else 0
    while attempt < len(plan):
        model = plan[attempt]
        t0 = time.time()
        r = FlowImage().execute({"prompt": prompt, "model": model, "aspect_ratio": aspect,
                                 "number_of_images": variants, "image_paths": refs,
                                 "output_path": str(out / f"{name}.png")})
        ok = r.success and (out / f"{name}.png").is_file()
        print(f"{name} try{attempt + 1} {model} refs={len(refs)} {'OK' if ok else 'FAIL ' + str(r.error)[:120]} "
              f"{time.time() - t0:.0f}s", flush=True)
        if model == "nano-pro":
            _pro_empty = 0 if ok else _pro_empty + ("without producing media" in str(r.error))
        if ok:
            (out / f"{name}.prompt.txt").write_text(prompt)
            (out / f"{name}.model.txt").write_text(model)
            return True
        if "QUOTA" in str(r.error) or (model == "nano-pro" and _pro_empty >= PRO_DOWN_AFTER):
            if model == FALLBACK_MODEL:
                print(f"{name} daily quota reached on both models, stopping (resume after the reset)", flush=True)
                sys.exit(3)
            attempt = plan.index(FALLBACK_MODEL)
            continue
        attempt += 1
    return False


def main() -> None:
    story = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[3:])
    style, chars = story["style"], story["characters"]
    cast = "CAST: " + " ".join(chars.values())
    fill = lambda text: text.format(**chars)  # noqa: E731

    for s in story.get("sheets", []):
        if only and s["id"] not in only:
            continue
        if s.get("path"):  # an existing library turnaround (Studio character): reuse it, spend no quota
            src = Path(s["path"])
            if src.is_file() and not (out / f"{s['id']}.png").is_file():
                shutil.copy2(src, out / f"{s['id']}.png")
            continue
        draw(out, s["id"], f"{style} {SHEET} {fill(s['prompt'])}", [], "16:9", 1)

    aspect = story.get("panel_aspect", "9:16")
    for p in story["panels"]:
        if only and p["id"] not in only:
            continue
        # refs: only adult cast sheets — Flow rejects pictures of children (and group shots with a child) as references
        refs = [str(out / f"{r}.png") for r in p.get("refs", []) if (out / f"{r}.png").is_file()]
        prompt = f"{style} {cast} {fill(p['prompt'])} {SINGLE} {FRAME[aspect]}"
        if not draw(out, p["id"], prompt, refs, aspect, int(story.get("variants", 2))):
            print(f"{p['id']} failed 3 times", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
