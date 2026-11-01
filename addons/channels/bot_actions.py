"""The owner's buttons under a channel post report, pressed in the factory bot's chat. factory-bot.service is the
only getUpdates reader of that token, so factory/service.py hands these callbacks here:

    chdel:<post id>      live post: deleted from the channel (every message of an album); a preview: taken off
    chpause:<channel>    the channel publishes nothing until resumed (the publisher re-reads the row before each post)
    chresume:<channel>   back on schedule
"""
from __future__ import annotations

import time

from addons.channels import config, publish

ACTIONS = ("chdel", "chpause", "chresume")


def handle(action: str, arg: str) -> tuple[str, list | None]:
    """(note for the owner, new inline keyboard for the report or None)."""
    db = config.db()
    if action in ("chpause", "chresume"):
        ch = db.channel(arg)
        if not ch:
            return f"⚠️ нет канала {arg}", None
        paused = action == "chpause"
        db.update("channels", "key", arg, paused=int(paused))
        db.event("control", f"{'пауза' if paused else 'возобновлён'} {arg} (кнопка в боте)")
        return (f"⏸ @{ch['username']} на паузе — посты не выходят" if paused else
                f"▶️ @{ch['username']} снова публикует по графику"), None
    if action == "chdel":
        if not arg.isdigit():
            return "⚠️ не тот пост", None
        p = db.one("SELECT * FROM posts WHERE id=?", (int(arg),))
        if not p:
            return "⚠️ пост не найден", None
        ch = db.channel(p["channel"]) or {}
        if p["status"] == "published":
            try:
                publish.delete(publish.channel_bot(ch), p["chat_id"], p.get("message_ids") or [])
            except Exception as e:  # noqa: BLE001
                return f"⚠️ не удалось удалить: {e}"[:190], None
            db.update("posts", "id", p["id"], status="deleted", updated=time.time(), error="удалён владельцем")
            db.event("control", f"удалён пост {p['id']} из @{ch.get('username')}")
            return f"🗑 Удалено из @{ch.get('username')}", None
        if p["status"] in ("previewed", "ready", "media", "draft"):
            db.update("posts", "id", p["id"], status="rejected", updated=time.time(), error="снят владельцем")
            return "🗑 Снято — в канал не пойдёт", None
        return f"уже {p['status']}", None
    return "", None


def keyboard(post_id: int, channel_key: str) -> list:
    db = config.db()
    ch = db.channel(channel_key) or {"key": channel_key}
    p = db.one("SELECT status FROM posts WHERE id=?", (post_id,)) or {}
    return publish.report_buttons(post_id, ch, p.get("status") == "published")
