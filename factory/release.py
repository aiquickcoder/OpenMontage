"""Turn a finished project into a release the phone farm can pull.

Layout served by nginx (FACTORY_RELEASES_DIR):

    pending/<id>/...            staged, waiting for the owner's approval
    <id>/final.mp4              approved release files
    <id>/checkpoint_publish.json  publish_log with export_path rewritten to local names
    index.json                  {"releases": [{id, approved_at, files: {name: {sha256, size}}, ...}]}

The release folder is read by phone-farm/farm/openmontage.py exactly like a
project directory.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

MIN_SECONDS, MAX_SECONDS = 15.0, 95.0


class ReleaseError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe(video: Path, runner=subprocess.run) -> dict[str, float]:
    proc = runner(["ffprobe", "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=width,height:format=duration", "-of", "json", str(video)],
                  capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise ReleaseError(f"ffprobe failed on {video}: {proc.stderr.strip()[:200]}")
    info = json.loads(proc.stdout or "{}")
    stream = (info.get("streams") or [{}])[0]
    return {"width": float(stream.get("width") or 0), "height": float(stream.get("height") or 0),
            "duration": float((info.get("format") or {}).get("duration") or 0)}


def check_video(video: Path, runner=subprocess.run) -> dict[str, float]:
    if not video.is_file():
        raise ReleaseError(f"render missing: {video}")
    p = probe(video, runner)
    if not p["height"] or p["width"] / p["height"] > 0.6:
        raise ReleaseError(f"not vertical 9:16: {int(p['width'])}x{int(p['height'])}")
    if not MIN_SECONDS <= p["duration"] <= MAX_SECONDS:
        raise ReleaseError(f"duration {p['duration']:.1f}s outside {MIN_SECONDS:.0f}-{MAX_SECONDS:.0f}s")
    return p


def _publish_entries(project: Path) -> list[dict[str, Any]]:
    path = project / "checkpoint_publish.json"
    if not path.is_file():
        raise ReleaseError(f"{path} missing")
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = ((data.get("artifacts") or {}).get("publish_log") or {}).get("entries") or []
    if not entries:
        raise ReleaseError("publish_log has no entries")
    return entries


def stage(project: Path, releases_dir: Path, release_id: str, runner=subprocess.run) -> dict[str, Any]:
    """Validate the project and copy its deliverables to pending/<release_id>."""
    project = Path(project)
    final = project / "renders" / "final.mp4"
    info = check_video(final, runner)
    entries = _publish_entries(project)

    dest = releases_dir / "pending" / release_id
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy2(final, dest / "final.mp4")
    rewritten = []
    for entry in entries:
        entry = dict(entry)
        src = Path(entry.get("export_path") or "renders/final.mp4")
        src = src if src.is_absolute() else project / src
        if src.is_file() and src.resolve() != final.resolve():
            check_video(src, runner)
            name = f"{entry.get('platform', 'export')}_{src.name}"
            shutil.copy2(src, dest / name)
            entry["export_path"] = name
        else:
            entry["export_path"] = "final.mp4"
        rewritten.append(entry)
    (dest / "checkpoint_publish.json").write_text(json.dumps(
        {"artifacts": {"publish_log": {"entries": rewritten}}}, ensure_ascii=False, indent=2), encoding="utf-8")

    first = rewritten[0].get("metadata_used") or {}
    return {
        "id": release_id,
        "dir": str(dest),
        "video": str(dest / "final.mp4"),
        "duration": round(info["duration"], 1),
        "caption": first.get("description") or first.get("title") or "",
        "hashtags": first.get("hashtags") or [],
        "platforms": [e.get("platform") for e in rewritten],
    }


def approve(releases_dir: Path, release_id: str, now: Optional[datetime] = None) -> dict[str, Any]:
    """Move pending/<id> live and append it to index.json (atomic replace)."""
    src = releases_dir / "pending" / release_id
    dest = releases_dir / release_id
    if not src.is_dir():
        raise ReleaseError(f"no pending release {release_id}")
    if dest.exists():
        raise ReleaseError(f"release {release_id} already published")
    src.rename(dest)
    files = {p.name: {"sha256": sha256(p), "size": p.stat().st_size}
             for p in sorted(dest.iterdir()) if p.is_file()}
    record = {"id": release_id,
              "approved_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
              "files": files}
    index_path = releases_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"releases": []}
    index["releases"].append(record)
    tmp = index_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(index_path)
    return record


def discard(releases_dir: Path, release_id: str) -> None:
    shutil.rmtree(releases_dir / "pending" / release_id, ignore_errors=True)
