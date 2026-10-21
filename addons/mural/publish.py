"""Write projects/<slug>/checkpoint_publish.json for the factory release step (factory/release.py).

    python addons/mural/publish.py projects/<slug> --caption "Хук одной строкой" --tags "#истории #90е #подруги"

Platforms: instagram, tiktok, youtube (all use renders/final.mp4). No «Реклама»/ERID — mural episodes are not ads.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

PLATFORMS = ("instagram", "tiktok", "youtube")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--caption", required=True)
    ap.add_argument("--tags", default="")
    ap.add_argument("--title", default="")
    a = ap.parse_args()
    proj = a.project.resolve()
    if not (proj / "renders" / "final.mp4").is_file():
        raise SystemExit("renders/final.mp4 missing — run build.py first")
    tags = [t if t.startswith("#") else f"#{t}" for t in a.tags.replace(",", " ").split() if t.strip("#")]
    entries = [{"platform": p, "export_path": "renders/final.mp4",
                "metadata_used": {"title": a.title or a.caption[:90], "description": a.caption, "hashtags": tags}}
               for p in PLATFORMS]
    data = {"stage": "publish", "status": "completed", "pipeline": "mural",
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "artifacts": {"publish_log": {"entries": entries}}}
    (proj / "checkpoint_publish.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("checkpoint_publish.json written:", len(entries), "platforms")


if __name__ == "__main__":
    main()
