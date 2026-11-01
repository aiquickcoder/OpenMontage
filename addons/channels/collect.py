"""The collector (zasnyali-collector.service): reads every source into raw_posts, as soon as it can.

Telegram (owner 2026-11-01: «Да разрешаю» — the reader account joins the PUBLIC source channels):
- joining, slowly: one channel every join_every_s (3 min by default), the most useful first (agencies' own channels,
  federal aggregators, Moscow, Kazan), muted and archived so the account's chat list stays usable; never above
  max_channels (480 of Telegram's 500) counting what the account was in already; channels it is already in are left
  as they are; a FloodWait pauses joining for that long; a private channel is never joined.
- real time: new posts of joined channels arrive as updates (events.NewMessage / events.Album);
- catching up: every 5 minutes every joined channel is read from its last seen post (updates can be lost on a
  reconnect); a channel not joined yet is read on its own interval (every_min: 3–30 min) — the old polling.
Feeds (RSS of agencies and authorities, sledcom.ru, tnv.ru, МВД on Rutube): every_min, 4–10 min.
Downloads: the media worker asks for a post's files through the downloads table — only this process holds the
Telegram session.

Posts are untrusted data: only read and stored, never followed.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import random
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from addons.channels import cluster, config, filters, tgread

log = logging.getLogger("channels.collect")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
KIND_ORDER = {"primary": 0, "aggregator": 1, "city": 2, "media": 3}
CATCHUP_S = 300
MSK = tgread.MSK


# -- storing ----------------------------------------------------------------------------------------------------------
def store(db, src: dict, row: dict) -> int:
    """One post into raw_posts (ignored when its link is known). Returns the new row id or 0."""
    text = row.get("text") or ""
    pl = filters.plain(text)
    why = filters.verdict(pl)
    ts = row.get("ts") or time.time()
    rid = db.insert("raw_posts", {
        "source_id": src["id"], "channel": src["ref"] if src["platform"] == "telegram" else src["title"],
        "post": row.get("post") or 0, "link": row["link"], "ts": ts, "text": text[:6000], "plain": pl[:4000],
        "views": int(row.get("views") or 0), "forwards": int(row.get("forwards") or 0),
        "reactions": int(row.get("reactions") or 0), "media": row.get("media") or [], "fwd": int(bool(row.get("fwd"))),
        "filtered": ", ".join(why), "read_at": time.time(), "kind": src["kind"], "trust": src["trust"],
        "region": src["region"], "sig": cluster.signature(pl)}, ignore=True)
    if rid:
        db.x("UPDATE sources SET posts_seen=posts_seen+1, last_post_ts=MAX(last_post_ts, ?) WHERE id=?", (ts, src["id"]))
    return rid


def tg_rows(username: str, rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if not (r.get("raw") or "").strip() and not r.get("media"):
            continue
        ts = datetime.fromisoformat(r["date"]).timestamp()
        out.append({**r, "ts": ts})
    return out


# -- feeds --------------------------------------------------------------------------------------------------------------
def fetch(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def strip_html(s: str) -> str:
    s = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", s or "", flags=re.I)
    s = re.sub(r"<br\s*/?>|</p>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", html.unescape(s)).strip()


def rss_items(raw: bytes) -> list[dict]:
    """Items of an RSS 2.0 / Atom feed: title, link, text, time, image."""
    root = ET.fromstring(raw)
    out = []
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for it in items[:60]:
        def tx(*names: str) -> str:
            for n in names:
                for child in it:
                    if child.tag.split("}")[-1] == n and (child.text or "").strip():
                        return child.text.strip()
            return ""
        link = tx("link")
        if not link:
            for child in it:
                if child.tag.split("}")[-1] == "link" and child.get("href"):
                    link = child.get("href")
        title = strip_html(tx("title"))
        body = strip_html(tx("full-text", "encoded", "description", "summary", "content"))
        when = tx("pubDate", "published", "updated", "date")
        try:
            ts = parsedate_to_datetime(when).timestamp() if when and not when[:4].isdigit() else \
                datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            ts = time.time()
        img = ""
        for child in it:
            tag = child.tag.split("}")[-1]
            if tag in ("enclosure", "content", "thumbnail") and (child.get("type") or "image").startswith("image") \
                    and child.get("url"):
                img = child.get("url")
                break
        if link and title:
            text = title if not body or body.startswith(title) else f"{title}. {body}"
            out.append({"link": link, "text": text[:4000], "ts": ts,
                        "media": [{"kind": "photo", "url": img}] if img else []})
    return out


def feed_rows(src: dict) -> list[dict]:
    if src["platform"] == "rss":
        return rss_items(fetch(src["ref"]))
    from addons.news import sources as ns

    spec = [src["platform"], src["ref"], False, src["kind"] == "primary"]
    out = []
    for p in ns.posts(spec):
        try:
            ts = datetime.fromisoformat(p["dt"]).replace(tzinfo=timezone.utc).timestamp()
        except (KeyError, ValueError):
            ts = time.time()
        media = [{"kind": "photo", "url": u} for u in (p.get("photos") or [])[:4] if u]
        media += [{"kind": "video", "ref": v} for v in (p.get("mp4s") or [])[:2] if v]
        out.append({"link": p["link"], "text": p["text"], "ts": ts, "views": p.get("views") or 0, "media": media})
    return out


def read_feed(db, src: dict) -> int:
    n = 0
    try:
        rows = feed_rows(src)
        horizon = time.time() - 12 * 3600
        for r in rows:
            if r["ts"] >= horizon:
                n += 1 if store(db, src, r) else 0
        db.update("sources", "id", src["id"], last_read=time.time(), error="")
    except Exception as e:  # noqa: BLE001 — a site down is noted on the source and retried next time
        db.update("sources", "id", src["id"], last_read=time.time(), error=f"{type(e).__name__}: {e}"[:300])
    return n


# -- Telegram -----------------------------------------------------------------------------------------------------------
class Collector:
    def __init__(self, db):
        self.db = db
        self.by_tg_id: dict[int, dict] = {}
        self.join_next = time.time() + 30
        self.join_pause_until = 0.0
        self.flood_until = 0.0
        self.n_channels: int | None = None
        self.counted = 0.0

    def tg_sources(self) -> list[dict]:
        return self.db.q("SELECT * FROM sources WHERE platform='telegram' AND enabled=1")

    def refresh_ids(self) -> None:
        self.by_tg_id = {int(s["tg_id"]): s for s in self.tg_sources() if s.get("tg_id")}

    async def poll(self, c, src: dict) -> int:
        """A channel not joined yet: everything after its last seen post. A joined one (catch-up): its last 15 posts
        of the last hour whatever was seen — an update lost on a reconnect may sit below the last seen id."""
        joined = bool(src.get("joined"))
        min_id = 0 if joined else int(src.get("last_id") or 0)
        hours = 1.5 if joined else (48 if min_id else 6)
        try:
            rows, info = await tgread.read_new(c, src["ref"], src, min_id, hours, 15 if joined else 40)
        except Exception as e:  # noqa: BLE001
            from telethon.errors import FloodWaitError

            if isinstance(e, FloodWaitError):
                self.flood_until = time.time() + e.seconds + 5
                log.warning("FloodWait %ss on %s", e.seconds, src["ref"])
            self.db.update("sources", "id", src["id"], last_read=time.time(), error=f"{type(e).__name__}: {e}"[:300])
            return 0
        n = 0
        for r in tg_rows(src["ref"], rows):
            n += 1 if store(self.db, src, r) else 0
        last = max([int(src.get("last_id") or 0)] + [max([r["post"]] + [m["post"] for m in r.get("media") or []])
                                                         for r in rows])
        fields = {"last_id": last, "last_read": time.time(), "error": ""}
        if info:
            fields.update(tg_id=info["tg_id"], tg_hash=info["tg_hash"])
        self.db.update("sources", "id", src["id"], **fields)
        if info:
            self.refresh_ids()
        return n

    async def on_message(self, event) -> None:
        msg = event.message
        if msg.grouped_id:  # the album handler takes it whole
            return
        await self._take([msg], event.chat_id)

    async def on_album(self, event) -> None:
        await self._take(list(event.messages), event.chat_id)

    async def _take(self, msgs: list, chat_id: int) -> None:
        from telethon import utils

        src = self.by_tg_id.get(utils.resolve_id(int(chat_id))[0])
        if not src:
            return
        rows = tgread.fold_albums([tgread.message_row(src["ref"], m) for m in msgs])
        for r in tg_rows(src["ref"], rows):
            store(self.db, src, r)
        top = max(m.id for m in msgs)
        self.db.x("UPDATE sources SET last_id=MAX(last_id, ?) WHERE id=?", (top, src["id"]))

    async def channels_count(self, c) -> int:
        n = 0
        async for d in c.iter_dialogs(ignore_migrated=True):
            if getattr(d.entity, "broadcast", False) or getattr(d.entity, "megagroup", False):
                n += 1
        return n

    async def join_one(self, c) -> None:
        """Join the next public source channel (owner's permission of 2026-11-01), muted and archived."""
        from telethon import functions, types
        from telethon.errors import ChannelPrivateError, ChannelsTooMuchError, FloodWaitError, InviteRequestSentError

        if time.time() < max(self.join_next, self.join_pause_until):
            return
        self.join_next = time.time() + float(config.setting(self.db, "join_every_s") or 180) * random.uniform(0.8, 1.3)
        if not config.setting(self.db, "join_enabled"):
            return
        todo = [s for s in self.tg_sources() if not s.get("joined") and time.time() - (s.get("join_tried") or 0) > 86400]
        if not todo:
            return
        limit = int(config.setting(self.db, "max_channels") or 480)
        if self.n_channels is None or time.time() - self.counted > 6 * 3600:
            self.n_channels, self.counted = await self.channels_count(c), time.time()
            self.db.set_setting("reader_channels", self.n_channels)
        if self.n_channels >= limit:
            self.join_pause_until = time.time() + 6 * 3600
            self.db.event("join", f"не вступаю: у аккаунта уже {limit}+ каналов")
            return
        todo.sort(key=lambda s: (KIND_ORDER.get(s["kind"], 9), -float(s["trust"] or 0)))
        src = todo[0]
        self.db.update("sources", "id", src["id"], join_tried=time.time())
        try:
            ent = await c.get_entity(tgread.peer_of(src, src["ref"]))
            if not getattr(ent, "username", None):
                self.db.update("sources", "id", src["id"], note="приватный — не вступаем без разрешения")
                return
            if getattr(ent, "left", True) is False:  # already a member: leave as it is
                self.db.update("sources", "id", src["id"], joined=1, tg_id=ent.id, tg_hash=ent.access_hash)
                self.refresh_ids()
                return
            await c(functions.channels.JoinChannelRequest(ent))
            await asyncio.sleep(2)
            try:
                await c(functions.account.UpdateNotifySettingsRequest(
                    peer=types.InputNotifyPeer(await c.get_input_entity(ent)),
                    settings=types.InputPeerNotifySettings(mute_until=2 ** 31 - 1, show_previews=False)))
                await c(functions.folders.EditPeerFoldersRequest(
                    folder_peers=[types.InputFolderPeer(await c.get_input_entity(ent), folder_id=1)]))
            except Exception as e:  # noqa: BLE001 — muting is a courtesy; the join itself counts
                log.info("mute/archive %s: %s", src["ref"], e)
            self.db.update("sources", "id", src["id"], joined=1, tg_id=ent.id, tg_hash=ent.access_hash, error="")
            self.n_channels += 1
            self.db.set_setting("reader_channels", self.n_channels)
            self.db.event("join", f"вступил в @{src['ref']} ({src['title']})")
            self.refresh_ids()
        except FloodWaitError as e:
            self.join_pause_until = time.time() + e.seconds + 60
            self.db.event("join", f"FloodWait {e.seconds} с на вступлении — пауза")
        except ChannelsTooMuchError:
            self.join_pause_until = time.time() + 24 * 3600
            self.db.event("join", "Telegram: слишком много каналов у аккаунта — вступления остановлены")
        except (ChannelPrivateError, InviteRequestSentError) as e:
            self.db.update("sources", "id", src["id"], note=f"не вступить: {type(e).__name__}")
        except Exception as e:  # noqa: BLE001
            self.db.update("sources", "id", src["id"], error=f"join: {type(e).__name__}: {e}"[:300])

    async def downloads(self, c) -> None:
        for d in self.db.q("SELECT * FROM downloads WHERE status='new' ORDER BY id LIMIT 3"):
            src = self.db.one("SELECT * FROM sources WHERE platform='telegram' AND ref=?", (d["channel"],)) or {}
            try:
                paths = await tgread.download(c, d["channel"], src, d["posts"], config.MEDIA / "src")
                self.db.update("downloads", "id", d["id"], status="done", paths=[str(p) for p in paths], done=time.time())
            except Exception as e:  # noqa: BLE001
                self.db.update("downloads", "id", d["id"], status="failed", error=f"{type(e).__name__}: {e}"[:300],
                               done=time.time())

    async def run(self) -> None:
        from telethon import events

        async with tgread.session() as c:
            self.refresh_ids()
            c.add_event_handler(self.on_message, events.NewMessage())
            c.add_event_handler(self.on_album, events.Album())
            self.db.event("collector", "сборщик запущен")
            loop = asyncio.get_running_loop()
            while True:
                now = time.time()
                if now >= self.flood_until:
                    for src in self.tg_sources():
                        every = CATCHUP_S if src.get("joined") else 60 * int(src.get("every_min") or 15)
                        if now - (src.get("last_read") or 0) < every:
                            continue
                        await self.poll(c, src)
                        await asyncio.sleep(1.0)
                        await self.downloads(c)  # a waiting media job never waits for a whole pass
                        if time.time() < self.flood_until:
                            break
                await self.downloads(c)
                await self.join_one(c)
                feeds = [s for s in self.db.q("SELECT * FROM sources WHERE platform!='telegram' AND enabled=1")
                         if time.time() - (s.get("last_read") or 0) >= 60 * int(s.get("every_min") or 5)]
                for s in feeds[:6]:  # in a thread with its own connection: sqlite3 connections are not shared
                    await loop.run_in_executor(None, lambda src=s: read_feed(config.db(), src))
                self.db.set_setting("collector_alive", time.time())
                await asyncio.sleep(3)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = config.db()
    from addons.channels import seed

    seed.seed(db)
    asyncio.run(Collector(db).run())


if __name__ == "__main__":
    main()
