"""The news base: every story the pick and the take-off watch saw, kept (owner 2026-11-04: «запоминай новости, которые
соберёшь, давай, может, в базу класть»).

SQLite <DATA>/news.db, table stories — one row a story (its key: the first post's ref), updated on every sighting:
when it was first and last seen, its hook / why / question / topic / place, the AI's score, the take-off and the
final score, its numbers (velocity, channels, reach, Google/Wikipedia demand), its links, where it came from
(pick / watch) and what became of it (status: seen → alerted → queued → made; skip and why). Later the films' TikTok
numbers (viral_stats) can be laid against it: what we saw, what we made, what flew.

    .venv/bin/python addons/news/newsbase.py [--days 2] [--top 40] [--status alerted] [--q "Долина"]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=3))
ORDER = {"seen": 0, "alerted": 1, "queued": 2, "made": 3}

SCHEMA = """CREATE TABLE IF NOT EXISTS stories (
    key TEXT PRIMARY KEY, first_seen REAL, last_seen REAL, seen_n INTEGER DEFAULT 0, source TEXT,
    hook TEXT, why TEXT, question TEXT, topic TEXT, place TEXT, region TEXT,
    score REAL, ai_score REAL, momentum REAL, best_score REAL,
    velocity REAL, channels INTEGER, channels_3h INTEGER, reach INTEGER, comments INTEGER, age_h REAL,
    demand TEXT, links TEXT, text TEXT, status TEXT DEFAULT 'seen', skip TEXT, film TEXT, angle TEXT)"""


def path() -> Path:
    import viral  # noqa: PLC0415 — the pick's data dir

    return viral.DATA / "news.db"


def db(p: Path | None = None) -> sqlite3.Connection:
    p = p or path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute(SCHEMA)
    if "angle" not in {r[1] for r in con.execute("PRAGMA table_info(stories)")}:  # a base from before the angle
        con.execute("ALTER TABLE stories ADD COLUMN angle TEXT")
    return con


def row_of(st: dict, source: str) -> dict:
    s, v = st.get("signals") or {}, st.get("ai") or {}
    seed = st.get("seed") or {}
    demand = list(s.get("google") or []) + list(s.get("demand") or [])
    return {"key": st["key"], "source": source, "hook": v.get("hook") or (seed.get("plain") or "")[:160],
            "why": v.get("why"), "question": v.get("question"), "topic": v.get("topic"), "place": st.get("place"),
            "region": st.get("region"), "score": st.get("score"), "ai_score": v.get("score"),
            "momentum": st.get("momentum"), "velocity": s.get("velocity"), "channels": s.get("channels"),
            "channels_3h": s.get("channels_3h"), "reach": s.get("reach"), "comments": s.get("comments"),
            "age_h": s.get("age_h"), "demand": json.dumps(demand, ensure_ascii=False),
            "links": json.dumps([p.get("link") for p in st.get("posts", [])][:12], ensure_ascii=False),
            "text": (seed.get("plain") or seed.get("text") or "")[:1200],
            "skip": ", ".join(str(x) for x in st.get("skip") or []) or None,
            "angle": json.dumps(v.get("angle"), ensure_ascii=False) if v.get("angle") else None}


def save(stories: list[dict], source: str, status: str = "seen", con: sqlite3.Connection | None = None) -> int:
    """Upsert the stories; the status only moves forward (seen → alerted → queued → made)."""
    con = con or db()
    now, n = time.time(), 0
    for st in stories:
        r = row_of(st, source)
        old = con.execute("SELECT status, best_score, first_seen, seen_n FROM stories WHERE key=?", (r["key"],)).fetchone()
        st_now = status if not old or ORDER.get(status, 0) >= ORDER.get(old["status"] or "seen", 0) else old["status"]
        best = max(x for x in (r["score"], old["best_score"] if old else None, -1) if x is not None)
        con.execute(
            "INSERT OR REPLACE INTO stories (key, first_seen, last_seen, seen_n, source, hook, why, question, topic, place,"
            " region, score, ai_score, momentum, best_score, velocity, channels, channels_3h, reach, comments, age_h,"
            " demand, links, text, status, skip, angle, film) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
            " (SELECT film FROM stories WHERE key=?))",
            (r["key"], old["first_seen"] if old else now, now, (old["seen_n"] if old else 0) + 1, r["source"],
             r["hook"], r["why"], r["question"], r["topic"], r["place"], r["region"], r["score"], r["ai_score"],
             r["momentum"], best if best >= 0 else None, r["velocity"], r["channels"], r["channels_3h"], r["reach"],
             r["comments"], r["age_h"], r["demand"], r["links"], r["text"], st_now, r["skip"], r["angle"], r["key"]))
        n += 1
    con.commit()
    return n


def mark(key: str, status: str, film: str | None = None, con: sqlite3.Connection | None = None) -> None:
    """A story's fate: queued for a film (its slug), made."""
    con = con or db()
    con.execute("UPDATE stories SET status=?, film=COALESCE(?, film) WHERE key=?", (status, film, key))
    con.commit()


def listing(days: float = 2, top: int = 40, status: str | None = None, q: str | None = None,
            con: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    con = con or db()
    sql = "SELECT * FROM stories WHERE last_seen > ?"
    args: list = [time.time() - days * 86400]
    if status:
        sql += " AND status=?"
        args.append(status)
    if q:
        sql += " AND (hook LIKE ? OR text LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " AND skip IS NULL ORDER BY best_score DESC LIMIT ?"
    args.append(top)
    return con.execute(sql, args).fetchall()


def main() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=2)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--status")
    ap.add_argument("--q")
    a = ap.parse_args()
    for i, r in enumerate(listing(a.days, a.top, a.status, a.q), 1):
        when = datetime.fromtimestamp(r["first_seen"], MSK).strftime("%d.%m %H:%M")
        dem = ", ".join(json.loads(r["demand"] or "[]"))
        print(f"{i:2}. [{r['best_score'] or 0:5.1f}] {r['status']:7} {when} · {r['place'] or r['region'] or '?'} · "
              f"{r['topic'] or '?'} · {r['channels'] or 0} кан. · охват {r['reach'] or 0:,}".replace(",", " ")
              + (f" · {dem}" if dem else "") + (f" · ролик {r['film']}" if r["film"] else ""))
        print(f"    {r['hook']}")
        ang = json.loads(r["angle"]) if r["angle"] else {}
        if ang.get("question"):
            print(f"    угол ({ang.get('kind')}): {ang['question']}")


if __name__ == "__main__":
    main()
