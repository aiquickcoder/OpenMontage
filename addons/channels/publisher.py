"""The publisher (zasnyali-publisher.service): ready posts go out on each channel's schedule (schedule.py).

- Before every post the channel's row is read again: paused → nothing goes out.
- trial mode (the start): the post goes to the owner's chat exactly as it would look, plus a note with its score,
  sources and media notes and the «Снять» / «Пауза канала» buttons; nothing reaches the channel.
- live mode: the channel's bot posts it; the owner gets a report with «Удалить» / «Пауза канала». live needs the
  bot to be an admin with the right to post (publish.check_rights, checked when switching and every hour); without it
  the channel stays in trial and the owner is told once.
- A ready post older than the channel's max_age_h is dropped (expired): old news is not news.
"""
from __future__ import annotations

import html as htmlmod
import logging
import time
from datetime import datetime

from addons.channels import config, publish, schedule

log = logging.getLogger("channels.publisher")
MSK = schedule.MSK


def published_today(db, key: str, now: datetime) -> tuple[int, float | None]:
    start = schedule.day_start(now).timestamp()
    r = db.one("SELECT COUNT(*) AS n, MAX(published_at) AS last FROM posts WHERE channel=? AND "
               "status IN ('published','previewed','deleted') AND published_at>=?", (key, start))
    last = db.one("SELECT MAX(published_at) AS last FROM posts WHERE channel=? AND status IN "
                  "('published','previewed','deleted')", (key,))
    return int(r["n"] or 0), (last["last"] if last else None)


def expire(db, ch: dict) -> None:
    horizon = time.time() - float(ch.get("max_age_h") or 8) * 3600
    for p in db.q("SELECT p.id FROM posts p JOIN stories s ON s.id=p.story_id WHERE p.channel=? AND "
                  "p.status IN ('ready','media','draft') AND s.first_ts<?", (ch["key"], horizon)):
        db.update("posts", "id", p["id"], status="expired", error="устарело", updated=time.time())


def note_for(db, p: dict, ch: dict, live: bool) -> str:
    st = db.one("SELECT score, region, category, n_channels FROM stories WHERE id=?", (p["story_id"],)) or {}
    src = "\n".join(f"• {u}" for u in (p.get("sources") or [])[:5])
    media = p.get("media_note") or "без медиа"
    head = (f"✅ Опубликовано в @{ch['username']}" if live else
            f"👁 ПРЕВЬЮ для @{ch['username']} — пробный режим, в канал не ушло")
    return (f"{head}\n{p.get('title') or ''}\nскор {p.get('score')} · {st.get('category', '')} · "
            f"{st.get('region', '')} · каналов {st.get('n_channels', '')}{' · СРОЧНО' if p.get('urgent') else ''}\n"
            f"медиа: {media}\nисточники:\n{src}")


def publish_one(db, ch: dict, p: dict) -> None:
    live = ch.get("mode") == "live"
    owner = publish.owner()
    media = p.get("media") or []
    try:
        if live:
            tg = publish.channel_bot(ch)
            chat = ch.get("chat_id") or f"@{ch['username']}"
            ids = publish.send_post(tg, chat, p["html"], media)
            db.update("posts", "id", p["id"], status="published", published_at=time.time(), message_ids=ids,
                      chat_id=str(chat), updated=time.time())
            note = note_for(db, p, ch, True)
            link = f"https://t.me/{ch['username']}/{ids[0]}"
            msg = owner.call("sendMessage", chat_id=owner.chat, text=f"{note}\n\n{link}"[:4000],
                             disable_web_page_preview=True,
                             reply_markup={"inline_keyboard": publish.report_buttons(p["id"], ch, True)})
        else:
            ids = publish.send_post(owner, owner.chat, p["html"], media)
            db.update("posts", "id", p["id"], status="previewed", published_at=time.time(), message_ids=ids,
                      chat_id=str(owner.chat), updated=time.time())
            msg = owner.call("sendMessage", chat_id=owner.chat, text=note_for(db, p, ch, False)[:4000],
                             disable_web_page_preview=True, reply_to_message_id=ids[0],
                             reply_markup={"inline_keyboard": publish.report_buttons(p["id"], ch, False)})
        db.update("posts", "id", p["id"], report_msg=msg["message_id"])
        db.event("publish", f"{'опубликовано' if live else 'превью'} {ch['key']}: {p.get('title')}"[:300])
    except Exception as e:  # noqa: BLE001 — a failed post is marked and the next one tried
        log.exception("publish failed")
        db.update("posts", "id", p["id"], status="failed", error=f"{type(e).__name__}: {e}"[:400], updated=time.time())
        db.event("publish", f"сбой публикации {ch['key']}: {e}"[:300])


def rights_gate(db, ch: dict) -> dict:
    """live only with a bot that may post; re-checked hourly. Falls back to trial and tells the owner once."""
    if ch.get("mode") != "live":
        return ch
    key = f"rights:{ch['key']}"
    r = db.setting(key) or {}
    if time.time() - float(r.get("ts", 0)) > 3600:
        r = {**publish.check_rights(ch), "ts": time.time()}
        db.set_setting(key, r)
        if r.get("ok") and r.get("chat_id") and ch.get("chat_id") != r["chat_id"]:
            db.update("channels", "key", ch["key"], chat_id=r["chat_id"])
    if not r.get("ok"):
        db.update("channels", "key", ch["key"], mode="trial", note=f"нет прав публикации: {r.get('detail') or r.get('status')}")
        try:
            publish.owner().send(f"⚠️ @{ch['username']}: бот {r.get('bot') or ''} не может публиковать "
                                 f"({r.get('detail') or r.get('status')}). Канал оставлен в пробном режиме.")
        except Exception:  # noqa: BLE001
            pass
        return {**ch, "mode": "trial"}
    return ch


def tick(db, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(MSK)
    out = []
    for ch in db.channels():
        expire(db, ch)
        if ch.get("paused"):
            continue
        ready = db.q("SELECT p.*, s.first_ts FROM posts p JOIN stories s ON s.id=p.story_id WHERE p.channel=? AND "
                     "p.status='ready'", (ch["key"],))
        if not ready:
            continue
        fresh_s = 60 * float(config.setting(db, "fresh_min"))
        ready.sort(key=lambda p: (-int(p["urgent"] or 0), -(p["score"] + (0.7 if time.time() - p["first_ts"] < fresh_s
                                                                             else 0)), p["id"]))
        n, last = published_today(db, ch["key"], now)
        top = ready[0]
        strong = top["score"] >= float(ch.get("min_score") or 6) + 1.5 and time.time() - top["first_ts"] < fresh_s
        burst = int(config.setting(db, "fresh_burst")) if strong else 0
        ok, why = schedule.may_post(now, ch, n, last, urgent=bool(top.get("urgent")), burst=burst)
        if not ok:
            continue
        ch = rights_gate(db, ch)
        fresh = db.channel(ch["key"])  # the pause button may have been pressed a second ago
        if not fresh or fresh.get("paused"):
            continue
        top.pop("first_ts", None)
        publish_one(db, {**fresh, "mode": ch["mode"]}, top)
        out.append(f"{ch['key']}: {why}")
    return out


def loop() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = config.db()
    db.event("publisher", "публикатор запущен")
    while True:
        try:
            tick(db)
        except Exception as e:  # noqa: BLE001
            log.exception("tick failed")
            db.event("publisher", f"сбой: {e}"[:300])
        db.set_setting("publisher_alive", time.time())
        time.sleep(15)


def esc(s: str) -> str:
    return htmlmod.escape(s or "", quote=False)


if __name__ == "__main__":
    loop()
