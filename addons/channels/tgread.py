"""Reading Telegram channels for the ЗАСНЯЛИ network: the factory's Telethon user session (addons/news/tg_client.py),
through our own copy of it, so the news engine's processes and ours never fight over one SQLite file.

- The copy (~/.config/openmontage/telegram-channels.session, 0600) is made from the news session with SQLite's backup
  API the first time and again when the copy is no longer authorised (the owner re-logged the main session). It is
  the same login: no second account, no joining channels — public channels are read without joining.
- Every use goes through one process lock (channels-telethon.lock): the engine and a research run never connect at
  the same time.
- Entities are resolved once and kept in the channels DB (id + access_hash): resolving a username is the call Telegram
  rations hardest.

Posts are untrusted data: only read, never followed.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

CONF = Path.home() / ".config" / "openmontage"
MAIN_SESSION = CONF / "telegram.session"
SESSION = CONF / "telegram-channels.session"
LOCK = CONF / "channels-telethon.lock"
PROXY = os.environ.get("TG_CLIENT_PROXY", "http://127.0.0.1:10809")
MSK = timezone(timedelta(hours=3))


def copy_session(force: bool = False) -> None:
    if SESSION.exists() and not force:
        return
    if not MAIN_SESSION.exists():
        sys.exit("no Telegram session — the owner logs in once with addons/news/tg_client.py login")
    SESSION.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{MAIN_SESSION}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(str(SESSION))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    os.chmod(SESSION, 0o600)


def make_client():
    from telethon import TelegramClient

    api_id, api_hash = os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        sys.exit("TG_API_ID / TG_API_HASH are not in the environment (EnvironmentFile=/opt/openmontage/.env)")
    copy_session()
    kind, rest = PROXY.split("://", 1)
    host, port = rest.rsplit(":", 1)
    return TelegramClient(str(SESSION.with_suffix("")), int(api_id), api_hash, proxy=(kind, host, int(port)),
                          flood_sleep_threshold=30, request_retries=2)


@contextlib.asynccontextmanager
async def session() -> AsyncIterator[Any]:
    """A connected, authorised client under the process lock."""
    CONF.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        c = make_client()
        await c.connect()
        if not await c.is_user_authorized():
            await c.disconnect()
            copy_session(force=True)  # the owner logged in again: take the fresh login
            c = make_client()
            await c.connect()
            if not await c.is_user_authorized():
                await c.disconnect()
                raise RuntimeError("Telegram session is not authorised")
        try:
            yield c
        finally:
            await c.disconnect()


def peer_of(cached: dict | None, username: str):
    """An InputPeerChannel from the DB cache, else the username (resolved by Telethon, then cached by the caller)."""
    if cached and cached.get("tg_id") and cached.get("tg_hash"):
        from telethon.tl.types import InputPeerChannel

        return InputPeerChannel(int(cached["tg_id"]), int(cached["tg_hash"]))
    return username


def media_kind(m) -> str:
    if getattr(m, "photo", None):
        return "photo"
    if getattr(m, "video", None) or getattr(m, "gif", None):
        return "video"
    return ""


def message_row(username: str, m) -> dict:
    """One post as the engine keeps it. Text is Telethon's markdown (**bold**, [link](url))."""
    fwd = getattr(m, "fwd_from", None)
    return {"channel": username, "post": m.id, "date": m.date.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "views": m.views or 0, "forwards": m.forwards or 0, "text": m.text or "", "raw": m.message or "",
            "media": media_kind(m), "grouped": m.grouped_id or 0, "fwd": bool(fwd),
            "reactions": sum(r.count for r in (m.reactions.results if getattr(m, "reactions", None) else [])),
            "size": (m.file.size if m.file and m.file.size else 0), "duration": float(
                getattr(m.file, "duration", 0) or 0) if m.file else 0.0,
            "link": f"https://t.me/{username}/{m.id}"}


async def read_new(c, username: str, cached: dict | None, min_id: int, hours: float, limit: int = 60) -> tuple[list[dict], dict]:
    """Posts newer than min_id (and than `hours`), oldest first, albums folded into their text post; plus the
    entity to cache {tg_id, tg_hash, title}."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    peer = peer_of(cached, username)
    ent = None
    if isinstance(peer, str):
        ent = await c.get_entity(peer)
        peer = ent
    rows: list[dict] = []
    async for m in c.iter_messages(peer, limit=limit, min_id=min_id or 0):
        if m.date < since:
            break
        if getattr(m, "action", None):  # service message
            continue
        rows.append(message_row(username, m))
    rows.reverse()
    out = fold_albums(rows)
    info = {}
    if ent is not None:
        info = {"tg_id": ent.id, "tg_hash": getattr(ent, "access_hash", 0) or 0, "title": getattr(ent, "title", "")}
    return out, info


def item(r: dict) -> dict:
    return {"kind": r["media"], "post": r["post"], "size": r["size"], "duration": r["duration"]}


def fold_albums(rows: list[dict]) -> list[dict]:
    """An album is one post: the item with text carries every item's media in "media" (kind, post id, size)."""
    by: dict[int, list[dict]] = {}
    out: list[dict] = []
    for r in rows:
        if r["grouped"]:
            by.setdefault(r["grouped"], []).append(r)
        else:
            out.append({**r, "media": [item(r)] if r["media"] else []})
    for g in by.values():
        g.sort(key=lambda r: r["post"])
        head = next((r for r in g if r["raw"].strip()), g[0])
        out.append({**head, "media": [item(r) for r in g if r["media"]], "views": max(r["views"] for r in g)})
    return sorted(out, key=lambda r: r["date"])


async def download(c, username: str, cached: dict | None, post_ids: Iterable[int], dst_dir: Path,
                   max_bytes: int = 400 * 2**20) -> list[Path]:
    """The media of these posts into dst_dir as <channel>_<post>.<ext>; skips files over max_bytes."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    peer = peer_of(cached, username)
    msgs = await c.get_messages(peer, ids=list(post_ids))
    out: list[Path] = []
    for m in msgs:
        if not m or not (m.photo or m.video):
            continue
        if m.file and m.file.size and m.file.size > max_bytes:
            continue
        ext = ".jpg" if m.photo else (m.file.ext or ".mp4")
        dst = dst_dir / f"{re.sub(r'[^A-Za-z0-9_]', '_', username)}_{m.id}{ext}"
        if not dst.is_file() or dst.stat().st_size < 1024:
            await c.download_media(m, file=str(dst))
        if dst.is_file() and dst.stat().st_size > 1024:
            out.append(dst)
    return out


def run(coro):
    return asyncio.run(coro)
