"""Telegram Bot API for the channels: a post as text / photo / video / album (HTML), its deletion, swapping its picture
for the cleaned video (editMessageMedia), and the owner's report with the «Удалить» / «Пауза канала» buttons.

Bots come from the environment by name (channels.bot_env, default TG_TOKEN); the owner's reports and previews go
through the factory bot (TG_TOKEN → TG_CHAT), whose button presses factory-bot.service hands to bot_actions.py.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from addons.channels import config
from factory.tg import VIDEO_LIMIT_BYTES, Telegram, squeeze, video_meta


def bot(env_name: str, chat: str = "") -> Telegram:
    return Telegram(os.environ.get(env_name or "TG_TOKEN", ""), chat, os.environ.get(config.BOT_PROXY_ENV, ""))


def owner() -> Telegram:
    return Telegram(os.environ.get(config.REPORT_TOKEN_ENV, ""), os.environ.get(config.REPORT_CHAT_ENV, ""),
                    os.environ.get(config.BOT_PROXY_ENV, ""))


def channel_bot(ch: dict) -> Telegram:
    return bot(ch.get("bot_env") or "TG_TOKEN", ch.get("chat_id") or f"@{ch['username']}")


def fit(path: Path) -> Path:
    """A video over the bot's 50 MB goes as a two-pass copy that fits (factory/tg.py squeeze)."""
    if path.suffix.lower() == ".mp4" and path.stat().st_size > VIDEO_LIMIT_BYTES:
        return squeeze(path)
    return path


def send_post(tg: Telegram, chat: str, html: str, media: list[dict]) -> list[int]:
    """The post as it appears in the channel; returns its message ids (an album has several)."""
    media = [m for m in media if m.get("path") and Path(m["path"]).is_file()]
    if not media:
        r = tg.call("sendMessage", chat_id=chat, text=html, parse_mode="HTML", disable_web_page_preview=True)
        return [r["message_id"]]
    if len(media) == 1:
        m = media[0]
        p = fit(Path(m["path"]))
        with open(p, "rb") as f:
            if m["kind"] == "video":
                r = tg.call("sendVideo", http_timeout=600, files={"video": (p.name, f, "video/mp4")}, chat_id=chat,
                            caption=html, parse_mode="HTML", supports_streaming=True, **video_meta(p))
            else:
                r = tg.call("sendPhoto", http_timeout=300, files={"photo": (p.name, f, "image/jpeg")}, chat_id=chat,
                            caption=html, parse_mode="HTML")
        return [r["message_id"]]
    files: dict[str, Any] = {}
    items = []
    handles = []
    try:
        for i, m in enumerate(media[:10]):
            p = fit(Path(m["path"]))
            fh = open(p, "rb")
            handles.append(fh)
            key = f"f{i}"
            files[key] = (p.name, fh, "video/mp4" if m["kind"] == "video" else "image/jpeg")
            it: dict[str, Any] = {"type": "video" if m["kind"] == "video" else "photo", "media": f"attach://{key}"}
            if m["kind"] == "video":
                it.update(supports_streaming=True, **video_meta(p))
            if i == 0:
                it.update(caption=html, parse_mode="HTML")
            items.append(it)
        r = tg.call("sendMediaGroup", http_timeout=900, files=files, chat_id=chat, media=items)
    finally:
        for fh in handles:
            fh.close()
    return [x["message_id"] for x in r]


def delete(tg: Telegram, chat: str, ids: list[int]) -> None:
    for mid in ids:
        tg.call("deleteMessage", chat_id=chat, message_id=mid)


def edit_media(tg: Telegram, chat: str, message_id: int, path: Path, kind: str, html: str) -> None:
    p = fit(path)
    with open(p, "rb") as f:
        item: dict[str, Any] = {"type": kind, "media": "attach://f0", "caption": html, "parse_mode": "HTML"}
        if kind == "video":
            item.update(supports_streaming=True, **video_meta(p))
        tg.call("editMessageMedia", http_timeout=600, files={"f0": (p.name, f, "video/mp4" if kind == "video" else
                                                                     "image/jpeg")},
                chat_id=chat, message_id=message_id, media=item)


def report_buttons(post_id: int, ch: dict, live: bool, max_sent: bool = False) -> list[list[dict[str, str]]]:
    pause = ({"text": "▶️ Возобновить канал", "callback_data": f"chresume:{ch['key']}"} if ch.get("paused") else
             {"text": "⏸ Пауза канала", "callback_data": f"chpause:{ch['key']}"})
    first = {"text": "🗑 Удалить" if live else "🗑 Снять", "callback_data": f"chdel:{post_id}"}
    rows = [[first, pause]]
    if max_sent:  # the MAX twin's copy has its own button (owner 2026-11-01)
        rows.append([{"text": "🗑 Удалить в MAX", "callback_data": f"chmaxdel:{post_id}"}])
    return rows


def check_rights(ch: dict) -> dict:
    """Is the channel's bot an admin that may post? {"ok", "bot", "status", "detail"} (getMe + getChatMember)."""
    tg = channel_bot(ch)
    if not tg.token:
        return {"ok": False, "detail": f"нет токена в {ch.get('bot_env') or 'TG_TOKEN'}"}
    try:
        me = tg.call("getMe")
        chat = tg.call("getChat", chat_id=f"@{ch['username']}")
        m = tg.call("getChatMember", chat_id=chat["id"], user_id=me["id"])
    except RuntimeError as e:
        name = locals().get("me", {}).get("username", "?")
        return {"ok": False, "bot": name, "detail": str(e)[:200]}
    ok = m.get("status") in ("administrator", "creator") and (m.get("status") == "creator" or m.get("can_post_messages"))
    return {"ok": bool(ok), "bot": me["username"], "chat_id": str(chat["id"]), "status": m.get("status"),
            "can_post": m.get("can_post_messages"), "can_edit": m.get("can_edit_messages"),
            "can_delete": m.get("can_delete_messages"), "detail": ""}


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)
