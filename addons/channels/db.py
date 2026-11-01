"""The channels engine's state: one SQLite file (WAL) shared by the engine, the media worker, the publisher, the
factory bot's buttons and the Studio «Каналы» page.

Tables
- sources    where news is read: platform (telegram | rutube | youtube | site | max), ref, region, kind
             (aggregator | city | primary | media), trust 0..1, read interval, the last post seen.
- raw_posts  every post read (text, views, media list), why the text filters dropped it, which story it joined.
- stories    one event told by one or more posts: the AI judge's verdict, region, score.
- channels   our channels: target posts a day, hour profile, quiet hours, pause, trial/live, the bot's env name.
- posts      our posts: draft → ready (media decided) → published / previewed; message ids for «Удалить».
- settings   editable texts and numbers (style guides, prompts, models) — defaults live in config.py.
- llm_usage  tokens and dollars a day per model and purpose.
- events     a short log the web page shows.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY, platform TEXT NOT NULL, ref TEXT NOT NULL, title TEXT DEFAULT '',
  region TEXT DEFAULT 'russia', kind TEXT DEFAULT 'aggregator', trust REAL DEFAULT 0.6, enabled INTEGER DEFAULT 1,
  every_min INTEGER DEFAULT 15, last_id INTEGER DEFAULT 0, last_read REAL DEFAULT 0, tg_id INTEGER, tg_hash INTEGER,
  subscribers INTEGER, note TEXT DEFAULT '', error TEXT DEFAULT '', added REAL DEFAULT 0, joined INTEGER DEFAULT 0,
  join_tried REAL DEFAULT 0, posts_seen INTEGER DEFAULT 0, last_post_ts REAL DEFAULT 0,
  UNIQUE(platform, ref));
CREATE TABLE IF NOT EXISTS raw_posts(
  id INTEGER PRIMARY KEY, source_id INTEGER, channel TEXT, post INTEGER, link TEXT UNIQUE, ts REAL, text TEXT,
  plain TEXT, views INTEGER DEFAULT 0, forwards INTEGER DEFAULT 0, reactions INTEGER DEFAULT 0, media TEXT DEFAULT '[]',
  fwd INTEGER DEFAULT 0, filtered TEXT DEFAULT '', story_id INTEGER, read_at REAL, kind TEXT DEFAULT '',
  trust REAL DEFAULT 0.6, region TEXT DEFAULT '', sig TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS raw_ts ON raw_posts(ts);
CREATE INDEX IF NOT EXISTS raw_story ON raw_posts(story_id);
CREATE TABLE IF NOT EXISTS stories(
  id INTEGER PRIMARY KEY, created REAL, updated REAL, first_ts REAL, title TEXT DEFAULT '', lead TEXT DEFAULT '',
  region TEXT DEFAULT '', category TEXT DEFAULT '', n_posts INTEGER DEFAULT 0, n_channels INTEGER DEFAULT 0,
  max_views INTEGER DEFAULT 0, has_primary INTEGER DEFAULT 0, has_media INTEGER DEFAULT 0, terms TEXT DEFAULT '[]',
  status TEXT DEFAULT 'new', judge TEXT DEFAULT '', score REAL DEFAULT 0, reason TEXT DEFAULT '',
  merged_into INTEGER, judged_at REAL, judged_posts INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS stories_status ON stories(status);
CREATE TABLE IF NOT EXISTS channels(
  key TEXT PRIMARY KEY, username TEXT, chat_id TEXT DEFAULT '', title TEXT DEFAULT '', regions TEXT DEFAULT '[]',
  per_day REAL DEFAULT 20, hours TEXT DEFAULT '[]', quiet_from INTEGER DEFAULT 1, quiet_to INTEGER DEFAULT 7,
  min_gap_min INTEGER DEFAULT 10, paused INTEGER DEFAULT 0, mode TEXT DEFAULT 'trial', bot_env TEXT DEFAULT 'TG_TOKEN',
  live_since REAL DEFAULT 0, ramp REAL DEFAULT 0.5, cta TEXT DEFAULT '', style TEXT DEFAULT '',
  min_score REAL DEFAULT 6.0, max_age_h REAL DEFAULT 8, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS posts(
  id INTEGER PRIMARY KEY, story_id INTEGER, channel TEXT, created REAL, status TEXT DEFAULT 'draft', html TEXT,
  score REAL DEFAULT 0, media TEXT DEFAULT '[]', media_note TEXT DEFAULT '', sources TEXT DEFAULT '[]',
  published_at REAL, message_ids TEXT DEFAULT '[]', chat_id TEXT DEFAULT '', report_msg INTEGER, error TEXT DEFAULT '',
  model TEXT DEFAULT '', title TEXT DEFAULT '', updated REAL, urgent INTEGER DEFAULT 0, upgrade TEXT DEFAULT '',
  facts TEXT DEFAULT '[]', UNIQUE(story_id, channel));
CREATE TABLE IF NOT EXISTS downloads(
  id INTEGER PRIMARY KEY, created REAL, channel TEXT, posts TEXT, status TEXT DEFAULT 'new', paths TEXT DEFAULT '[]',
  error TEXT DEFAULT '', done REAL);
CREATE INDEX IF NOT EXISTS posts_status ON posts(status);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS llm_usage(
  day TEXT, model TEXT, purpose TEXT, calls INTEGER DEFAULT 0, tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0, cost REAL DEFAULT 0, PRIMARY KEY(day, model, purpose));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, text TEXT);
"""

JSON_FIELDS = {"media", "terms", "judge", "regions", "hours", "sources", "message_ids", "sig", "posts", "paths",
               "upgrade", "facts"}


class DB:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA busy_timeout=30000")
        self.con.executescript(SCHEMA)

    # -- generic ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _row(r: Optional[sqlite3.Row]) -> Optional[dict]:
        if r is None:
            return None
        d = dict(r)
        for k in JSON_FIELDS & d.keys():
            v = d[k]
            if isinstance(v, str) and v[:1] in "[{":
                try:
                    d[k] = json.loads(v)
                except ValueError:
                    pass
        return d

    def q(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        return [self._row(r) for r in self.con.execute(sql, tuple(args)).fetchall()]

    def one(self, sql: str, args: Iterable[Any] = ()) -> Optional[dict]:
        return self._row(self.con.execute(sql, tuple(args)).fetchone())

    def x(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.con.execute(sql, tuple(args))

    @contextmanager
    def tx(self) -> Iterator[None]:
        self.con.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        self.con.execute("COMMIT")

    @staticmethod
    def _val(v: Any) -> Any:
        return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v

    def insert(self, table: str, row: dict, ignore: bool = False) -> int:
        keys = list(row)
        sql = (f"INSERT {'OR IGNORE ' if ignore else ''}INTO {table}({','.join(keys)}) "
               f"VALUES({','.join('?' * len(keys))})")
        cur = self.x(sql, [self._val(row[k]) for k in keys])
        return cur.lastrowid if cur.rowcount else 0

    def update(self, table: str, key: str, value: Any, **fields: Any) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE {table} SET {sets} WHERE {key}=?", [self._val(v) for v in fields.values()] + [value])

    # -- settings ----------------------------------------------------------------------------------------------------
    def setting(self, key: str, default: Any = None) -> Any:
        r = self.con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if r is None:
            return default
        try:
            return json.loads(r[0])
        except (TypeError, ValueError):
            return r[0]

    def set_setting(self, key: str, value: Any) -> None:
        self.x("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value, ensure_ascii=False)))

    # -- log ----------------------------------------------------------------------------------------------------------
    def event(self, kind: str, text: str) -> None:
        self.x("INSERT INTO events(ts, kind, text) VALUES(?, ?, ?)", (time.time(), kind, text[:2000]))

    def usage(self, model: str, purpose: str, tin: int, tout: int, cost: float) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))
        self.x("INSERT INTO llm_usage(day, model, purpose, calls, tokens_in, tokens_out, cost) VALUES(?,?,?,1,?,?,?) "
               "ON CONFLICT(day, model, purpose) DO UPDATE SET calls=calls+1, tokens_in=tokens_in+excluded.tokens_in, "
               "tokens_out=tokens_out+excluded.tokens_out, cost=cost+excluded.cost", (day, model, purpose, tin, tout, cost))

    def channel(self, key: str) -> Optional[dict]:
        return self.one("SELECT * FROM channels WHERE key=?", (key,))

    def channels(self) -> list[dict]:
        return self.q("SELECT * FROM channels ORDER BY CASE key WHEN 'news' THEN 0 WHEN 'moscow' THEN 1 ELSE 2 END")
