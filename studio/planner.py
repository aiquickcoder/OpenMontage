"""Episode planner: a calendar of episodes per world, drafted by Claude or written by hand,
released into the factory queue on their air date.

Episodes live in studio.db (`episodes`). Status: draft -> approved -> queued (brief created)
-> done | failed (mirrors the brief). The factory worker calls release_due() every loop:
approved episodes whose air_date has come become briefs (world + episode options), at most
PLANNER_DAILY_LIMIT per UTC day and not before PLANNER_RELEASE_HOUR_UTC.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from studio import auth, characters, projects, worlds

STATUSES = ("draft", "approved", "queued", "done", "failed")
EDITABLE = ("draft", "approved")
BRIEF_DONE = ("awaiting_approval", "approved")
BRIEF_FAILED = ("failed", "rejected")
BRIEF_PAUSED = ("budget",)  # out of the day's Flow credits: resumed by release_due the next day

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL,
    air_date TEXT NOT NULL,
    title TEXT NOT NULL,
    logline TEXT NOT NULL DEFAULT '',
    synopsis TEXT NOT NULL DEFAULT '',
    characters TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    source TEXT NOT NULL DEFAULT 'manual',
    brief_id TEXT,
    released_at TEXT,
    resumed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS episodes_date ON episodes (air_date);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def daily_limit() -> int:
    raw = os.environ.get("PLANNER_DAILY_LIMIT", "1").split("#")[0].strip()
    return int(raw) if raw.isdigit() else 1


def release_hour() -> int:
    raw = os.environ.get("PLANNER_RELEASE_HOUR_UTC", "6").split("#")[0].strip()
    return min(23, int(raw)) if raw.isdigit() else 6


class EpisodeStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    def _migrate(self) -> None:
        with self._conn() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(episodes)")}
            if "resumed_at" not in cols:
                c.execute("ALTER TABLE episodes ADD COLUMN resumed_at TEXT")

    @classmethod
    def of(cls, settings: Any) -> "EpisodeStore":
        return cls(settings.db_path if hasattr(settings, "factory") else settings.data_dir / "studio.db")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["characters"] = json.loads(out["characters"] or "[]")
        return out

    def add(self, world_id: str, air_date: str, title: str, logline: str = "", synopsis: str = "",
            chars: Optional[list[str]] = None, notes: str = "", source: str = "manual",
            status: str = "draft") -> dict[str, Any]:
        ep_id = f"ep{datetime.now(timezone.utc):%Y%m%d%H%M%S}{secrets.token_hex(3)}"
        now = _now()
        with self._conn() as c:
            c.execute("INSERT INTO episodes (id, world_id, air_date, title, logline, synopsis, characters, notes, "
                      "status, source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (ep_id, world_id, air_date, title, logline, synopsis,
                       json.dumps(chars or [], ensure_ascii=False), notes, status, source, now, now))
        return self.get(ep_id)

    def get(self, ep_id: str) -> dict[str, Any]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM episodes WHERE id = ?", (ep_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown episode")
        return self._row(row)

    def update(self, ep_id: str, **fields: Any) -> dict[str, Any]:
        if "characters" in fields:
            fields["characters"] = json.dumps(fields["characters"], ensure_ascii=False)
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE episodes SET {cols} WHERE id = ?", (*fields.values(), ep_id))
        return self.get(ep_id)

    def delete(self, ep_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM episodes WHERE id = ?", (ep_id,))

    def list(self, start: str = "", end: str = "", world_id: str = "", status: str = "") -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM episodes WHERE 1=1", []
        if start:
            sql += " AND air_date >= ?"
            args.append(start)
        if end:
            sql += " AND air_date <= ?"
            args.append(end)
        if world_id:
            sql += " AND world_id = ?"
            args.append(world_id)
        if status:
            sql += " AND status = ?"
            args.append(status)
        with self._conn() as c:
            return [self._row(r) for r in c.execute(sql + " ORDER BY air_date, created_at", args)]

    def recent_loglines(self, world_id: str, limit: int = 30) -> list[str]:
        with self._conn() as c:
            rows = c.execute("SELECT title, logline FROM episodes WHERE world_id = ? ORDER BY air_date DESC LIMIT ?",
                             (world_id, limit)).fetchall()
        return [f"{r['title']}: {r['logline']}" for r in rows]

    def released_on(self, day: str) -> int:
        """Production slots used on `day`: new releases plus resumed (multi-day) episodes."""
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM episodes WHERE released_at LIKE ? OR resumed_at LIKE ?",
                             (f"{day}%", f"{day}%")).fetchone()[0]


# -- release into the factory queue ----------------------------------------------------------
def brief_text(ep: dict[str, Any]) -> str:
    parts = [f"Эпизод «{ep['title']}»."]
    if ep.get("logline"):
        parts.append(ep["logline"])
    if ep.get("synopsis"):
        parts.append(ep["synopsis"])
    return "\n\n".join(parts)


def _free_brief_id(briefs: Any, ep: dict[str, Any]) -> str:
    """make_id() is per-second; several episodes released in one loop must not collide."""
    from factory.db import make_id

    base = make_id(f"{ep['world_id']} {ep['title']}")
    cand, n = base, 2
    while briefs.get(cand) is not None:
        cand, n = f"{base}-{n}", n + 1
    return cand


def sync_status(eps: "EpisodeStore", briefs: Any) -> None:
    for ep in eps.list(status="queued"):
        brief = briefs.get(ep["brief_id"]) if ep.get("brief_id") else None
        if brief is None:
            continue
        if brief["state"] in BRIEF_DONE:
            eps.update(ep["id"], status="done")
        elif brief["state"] in BRIEF_FAILED:
            eps.update(ep["id"], status="failed")


def release_due(settings: Any, briefs: Any, now: Optional[datetime] = None) -> list[str]:
    """Turn approved episodes whose air date has come into factory briefs. Returns brief ids."""
    now = now or datetime.now(timezone.utc)
    eps = EpisodeStore.of(settings)
    sync_status(eps, briefs)
    if now.hour < release_hour():
        return []
    today = now.date().isoformat()
    room = daily_limit() - eps.released_on(today)
    released: list[str] = []
    # Episodes that ran out of yesterday's credits continue first (one slot each).
    for ep in eps.list(status="queued"):
        if room <= 0:
            break
        brief = briefs.get(ep["brief_id"]) if ep.get("brief_id") else None
        if not brief or brief["state"] not in BRIEF_PAUSED:
            continue
        if (ep.get("resumed_at") or "").startswith(today) or (ep.get("released_at") or "").startswith(today):
            continue
        briefs.move(brief["id"], "queued", detail="planner: resumed with the new day's Flow credits")
        eps.update(ep["id"], resumed_at=now.isoformat(timespec="seconds"))
        released.append(brief["id"])
        room -= 1
    for ep in eps.list(end=today, status="approved"):
        if room <= 0:
            break
        opts = {worlds.OPTION_KEY: ep["world_id"], "episode": ep["id"]}
        world_dir = worlds.worlds_dir(settings) / ep["world_id"] / "world.json"
        if world_dir.is_file():
            duration = json.loads(world_dir.read_text(encoding="utf-8")).get("duration")
            if duration:
                opts["duration"] = str(duration)
        ids = characters.normalize_ids(ep.get("characters"))
        if ids:
            opts[characters.OPTION_KEY] = ",".join(ids)
        brief_id = briefs.add(brief_text(ep), opts, brief_id=_free_brief_id(briefs, ep))
        eps.update(ep["id"], status="queued", brief_id=brief_id, released_at=now.isoformat(timespec="seconds"))
        released.append(brief_id)
        room -= 1
    return released


# -- Claude week plan -----------------------------------------------------------------------
def plan_week(settings: Any, world_id: str, start: date, days: int, wishes: str) -> dict[str, Any]:
    store = worlds.WorldStore.of(settings)
    world = store.load(world_id)
    cast = worlds.world_characters(settings, world_id)
    if not cast:
        raise RuntimeError("в мире нет персонажей")
    eps = EpisodeStore.of(settings)
    world_text = json.dumps({k: world.get(k) for k in worlds.FIELDS}, ensure_ascii=False, indent=1)
    chars = "\n".join(f"- {c['id']}: {c.get('name')} ({c.get('role')}) — {c.get('description')}" for c in cast)
    past = "\n".join(f"- {x}" for x in eps.recent_loglines(world_id)) or "(none)"
    answer = worlds.ask_claude_json(settings, worlds.PLAN_PROMPT.format(
        days=days, world=world_text, chars=chars, past=past, wishes=wishes or "(none)"))
    valid = {c["id"] for c in cast}
    created = []
    for i, item in enumerate((answer.get("episodes") or [])[:days]):
        if not isinstance(item, dict) or not item.get("title"):
            continue
        ids = [x for x in characters.normalize_ids(item.get("characters")) if x in valid] or sorted(valid)
        ep = eps.add(world_id, (start + timedelta(days=i)).isoformat(), str(item["title"])[:200],
                     str(item.get("logline") or "")[:1000], str(item.get("synopsis") or "")[:4000],
                     ids, source="ai")
        created.append(ep["id"])
    if not created:
        raise RuntimeError("Claude не вернул эпизодов")
    return {"episodes": created}


# -- API ---------------------------------------------------------------------------------
class EpisodeIn(BaseModel):
    world_id: str = Field(min_length=1, max_length=60)
    air_date: date
    title: str = Field(min_length=1, max_length=200)
    logline: str = Field(default="", max_length=1000)
    synopsis: str = Field(default="", max_length=4000)
    characters: list[str] = Field(default_factory=list, max_length=12)
    notes: str = Field(default="", max_length=2000)


class EpisodePatch(BaseModel):
    air_date: Optional[date] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    logline: Optional[str] = Field(default=None, max_length=1000)
    synopsis: Optional[str] = Field(default=None, max_length=4000)
    characters: Optional[list[str]] = Field(default=None, max_length=12)
    notes: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[str] = None  # draft | approved


class PlanWeekIn(BaseModel):
    start_date: date
    days: int = Field(default=7, ge=1, le=14)
    wishes: str = Field(default="", max_length=2000)


router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])


def _eps(request: Request) -> EpisodeStore:
    return EpisodeStore.of(request.app.state.settings)


def _check_world(request: Request, world_id: str) -> str:
    world_id = projects.safe_id(world_id)
    worlds.WorldStore.of(request.app.state.settings).load(world_id)
    return world_id


@router.get("/episodes")
def list_episodes(request: Request, start: str = "", end: str = "", world: str = "", status: str = "") -> list[dict]:
    eps = _eps(request)
    sync_status(eps, request.app.state.store)
    return eps.list(start, end, world, status)


@router.post("/episodes", status_code=201)
def create_episode(body: EpisodeIn, request: Request) -> dict:
    world_id = _check_world(request, body.world_id)
    return _eps(request).add(world_id, body.air_date.isoformat(), body.title.strip(), body.logline.strip(),
                             body.synopsis.strip(), characters.normalize_ids(body.characters), body.notes.strip())


@router.patch("/episodes/{ep_id}")
def patch_episode(ep_id: str, body: EpisodePatch, request: Request) -> dict:
    eps = _eps(request)
    ep = eps.get(projects.safe_id(ep_id))
    if ep["status"] not in EDITABLE:
        raise HTTPException(status_code=409, detail="эпизод уже в производстве")
    fields: dict[str, Any] = {}
    for key in ("title", "logline", "synopsis", "notes"):
        value = getattr(body, key)
        if value is not None:
            fields[key] = value.strip()
    if body.air_date is not None:
        fields["air_date"] = body.air_date.isoformat()
    if body.characters is not None:
        fields["characters"] = characters.normalize_ids(body.characters)
    if body.status is not None:
        if body.status not in EDITABLE:
            raise HTTPException(status_code=422, detail="status must be draft or approved")
        fields["status"] = body.status
    return eps.update(ep["id"], **fields) if fields else ep


@router.delete("/episodes/{ep_id}")
def delete_episode(ep_id: str, request: Request) -> dict:
    eps = _eps(request)
    ep = eps.get(projects.safe_id(ep_id))
    if ep["status"] not in EDITABLE:
        raise HTTPException(status_code=409, detail="эпизод уже в производстве")
    eps.delete(ep["id"])
    return {"ok": True}


@router.post("/worlds/{world_id}/plan-week", status_code=201)
def plan_week_route(world_id: str, body: PlanWeekIn, request: Request) -> dict:
    world_id = _check_world(request, world_id)
    settings = request.app.state.settings
    if not worlds.world_characters(settings, world_id):
        raise HTTPException(status_code=422, detail="сначала создайте персонажей мира")
    task_id = worlds.run_in_background(
        settings, "week_plan", {"world_id": world_id, "start_date": body.start_date.isoformat(), "days": body.days},
        lambda: plan_week(settings, world_id, body.start_date, body.days, body.wishes.strip()))
    return {"task_id": task_id}


__all__ = ["EpisodeStore", "router", "release_due", "plan_week", "sync_status", "brief_text"]
