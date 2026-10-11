"""Read-only views of pipeline projects for the Studio (stages, logs, credits, media paths).

Handles both layouts the agent produces: checkpoints in projects/<id>/ or nested in
projects/<id>/<id>/ (assets and renders stay in the outer dir).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from factory.runner import completed_stages

CARTOON_AD_STAGES = ("idea", "proposal", "script", "character_design", "scene_plan",
                     "assets", "edit", "compose", "publish")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def safe_id(value: str) -> str:
    if not ID_RE.match(value or "") or ".." in value:
        raise HTTPException(status_code=400, detail="invalid id")
    return value


def project_dir(projects_root: Path, project_id: str) -> Optional[Path]:
    path = projects_root / safe_id(project_id)
    return path if path.is_dir() else None


def board_dir(project: Path) -> Path:
    """The dir holding checkpoints (nested layout when the outer one has none)."""
    nested = project / project.name
    if not list(project.glob("checkpoint_*.json")) and list(nested.glob("checkpoint_*.json")):
        return nested
    return project


def safe_file(project: Path, rel: str) -> Path:
    """Resolve `rel` inside the project (outer dir first, then the nested one); 403 on escapes."""
    for base in (project, project / project.name):
        target = (base / rel).resolve()
        try:
            target.relative_to(project.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="path escapes project")
        if target.is_file():
            return target
    raise HTTPException(status_code=404, detail="file not found")


def ledger(project: Path) -> dict[str, Any]:
    for base in (project, project / project.name):
        path = base / "flow_credits.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = data.get("entries", [])
            return {"budget": data.get("budget"),
                    "spent": sum(e.get("credits", 0) for e in entries if e.get("status") == "charged"),
                    "reserved": sum(e.get("credits", 0) for e in entries if e.get("status") == "reserved"),
                    "entries": len(entries)}
    return {}


def stages(project: Optional[Path], active: bool = False) -> list[dict[str, Any]]:
    """Stage rail; the first unfinished stage is `current` while the brief is running."""
    done = set(completed_stages(project)) if project else set()
    rows = []
    for name in CARTOON_AD_STAGES:
        status = "done" if name in done else "pending"
        rows.append({"stage": name, "status": status})
    if active:
        for row in rows:
            if row["status"] != "done":
                row["status"] = "current"
                break
    return rows


def flow_events(project: Path, limit: int = 40) -> list[dict[str, Any]]:
    """Recent Flow tool calls (events.jsonl finish rows), newest last."""
    rows: list[dict[str, Any]] = []
    for base in (project, project / project.name):
        path = base / "events.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("event") == "finish":
                rows.append({k: ev.get(k) for k in ("ts", "tool", "scene_id", "success", "output_path", "duration_s")})
    rows.sort(key=lambda r: r.get("ts") or "")
    return rows[-limit:]


def tail(path: Path, lines: int = 120) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def autopilot_log(project: Path, lines: int = 120) -> str:
    for base in (project, project / project.name):
        out = tail(base / "autopilot_log.md", lines)
        if out:
            return out
    return ""


def images(project: Path, limit: int = 60) -> list[str]:
    """Generated stills (character sheets, locations, keyframes), relative to the project."""
    found = []
    for sub in ("assets/images", "assets/characters"):
        d = project / sub
        if d.is_dir():
            found += [str(p.relative_to(project)) for p in sorted(d.rglob("*"))
                      if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and not p.name.startswith("_")]
    return found[:limit]


def clips(project: Path) -> list[str]:
    found = []
    for sub in ("assets/video", "renders"):
        d = project / sub
        if d.is_dir():
            found += [str(p.relative_to(project)) for p in sorted(d.rglob("*.mp4"))]
    return found
