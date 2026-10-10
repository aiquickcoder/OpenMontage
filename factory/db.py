"""Brief queue (SQLite). One row per brief; every transition is also logged to events."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

STATES = ("queued", "running", "awaiting_approval", "approved", "rejected", "failed", "budget")
TRANSITIONS = {
    "queued": {"running", "rejected"},
    "running": {"awaiting_approval", "failed", "budget"},
    "awaiting_approval": {"approved", "rejected"},
    "budget": {"queued", "rejected"},
    "failed": {"queued", "rejected"},
    "approved": set(),
    "rejected": set(),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS briefs (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    options TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL,
    project_dir TEXT,
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id TEXT NOT NULL,
    state TEXT NOT NULL,
    detail TEXT,
    at TEXT NOT NULL
);
"""


class InvalidTransition(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_id(text: str, when: Optional[datetime] = None) -> str:
    when = when or datetime.now(timezone.utc)
    translit = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                             "abvgdeejziiklmnoprstufhccss_y_eua")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().translate(translit)).strip("-")[:32].strip("-")
    return f"{when:%Y%m%d-%H%M%S}-{slug or 'brief'}"


class BriefStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def add(self, text: str, options: Optional[dict[str, Any]] = None, brief_id: Optional[str] = None) -> str:
        brief_id = brief_id or make_id(text)
        now = _now()
        with self._conn() as c:
            c.execute("INSERT INTO briefs (id, text, options, state, created_at, updated_at) "
                      "VALUES (?, ?, ?, 'queued', ?, ?)",
                      (brief_id, text, json.dumps(options or {}, ensure_ascii=False), now, now))
            c.execute("INSERT INTO events (brief_id, state, at) VALUES (?, 'queued', ?)", (brief_id, now))
        return brief_id

    def get(self, brief_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, states: Optional[tuple[str, ...]] = None, limit: int = 20) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM briefs", []
        if states:
            q += f" WHERE state IN ({','.join('?' * len(states))})"
            args = list(states)
        q += " ORDER BY created_at DESC LIMIT ?"
        with self._conn() as c:
            return [self._row(r) for r in c.execute(q, (*args, limit)).fetchall()]

    def claim_next(self) -> Optional[dict[str, Any]]:
        """Atomically move the oldest queued brief to running."""
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM briefs WHERE state = 'queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                c.execute("COMMIT")
                return None
            now = _now()
            c.execute("UPDATE briefs SET state = 'running', updated_at = ? WHERE id = ?", (now, row["id"]))
            c.execute("INSERT INTO events (brief_id, state, at) VALUES (?, 'running', ?)", (row["id"], now))
            c.execute("COMMIT")
        return self.get(row["id"])

    def move(self, brief_id: str, state: str, *, detail: Optional[str] = None,
             project_dir: Optional[str] = None) -> None:
        current = self.get(brief_id)
        if current is None:
            raise KeyError(brief_id)
        if state not in TRANSITIONS[current["state"]]:
            raise InvalidTransition(f"{brief_id}: {current['state']} → {state} is not allowed")
        now = _now()
        with self._conn() as c:
            c.execute("UPDATE briefs SET state = ?, detail = ?, project_dir = COALESCE(?, project_dir), "
                      "updated_at = ? WHERE id = ?", (state, detail, project_dir, now, brief_id))
            c.execute("INSERT INTO events (brief_id, state, detail, at) VALUES (?, ?, ?, ?)",
                      (brief_id, state, detail, now))

    def recover_running(self) -> list[str]:
        """After a crash: running briefs are failed, never re-run (credits may be spent)."""
        stuck = [b["id"] for b in self.list(("running",), limit=1000)]
        for brief_id in stuck:
            self.move(brief_id, "failed", detail="interrupted (worker restarted); check credits before requeue")
        return stuck

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["options"] = json.loads(d.get("options") or "{}")
        return d
