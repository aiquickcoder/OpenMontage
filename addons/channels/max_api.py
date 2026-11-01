"""MAX (max.ru) Bot API for the ЗАСНЯЛИ channels: the same ready posts into the channels' MAX twins.

Owner 2026-11-01: «автопостинг настроим в каналы — тг / макс». Written to dev.max.ru (checked 2026-11-01):
- base https://botapi.max.ru (CHANNELS_MAX_API). The docs now name platform-api2.max.ru, but its certificate comes
  from the «Russian Trusted Root CA» (Минцифры), which the server does not trust (checked 2026-11-01: CERTIFICATE_
  VERIFY_FAILED), while botapi.max.ru and platform-api.max.ru answer with a public certificate (an invalid token
  gets 401 verify.token there). To move to platform-api2: CHANNELS_MAX_API=https://platform-api2.max.ru and
  CHANNELS_MAX_CA=<PEM of the Russian root>. Reached directly (a Russian service, no VPN);
- auth: header `Authorization: <token>` (query tokens are no longer accepted); the token lives in the environment
  under the name the channel row gives (max_bot_env, default MAX_BOT_TOKEN) — never in the code or the DB;
- POST /messages?chat_id=<id> {"text" ≤ 4000, "format": "html", "attachments": [...]} → message.body.mid;
- media: POST /uploads?type=image|video → {"url", "token"?}; the file goes to url as multipart field "data"; an
  image's token comes back as photos.<id>.token, a video's is the one from /uploads; a message sent too early gets
  `attachment.not.ready` — retried with growing pauses;
- DELETE /messages?message_id=<mid> → {"success": true}; GET /me; GET /chats/{chat_id}/members/me → is_admin,
  is_owner, permissions (write, edit, delete …); a channel's chat_id comes with the bot_added update (GET /updates).
Nothing is sent while a channel's MAX twin is off (max_enabled = 0, the default) or its token is missing.
"""
from __future__ import annotations

import html as htmlmod
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from addons.channels import config

API = config.env("CHANNELS_MAX_API", "https://botapi.max.ru").rstrip("/")
MAX_TEXT = 4000
NOT_READY_WAITS = (2, 4, 8, 16, 30)


class MaxError(RuntimeError):
    pass


def token_of(ch: dict) -> str:
    return os.environ.get(ch.get("max_bot_env") or "MAX_BOT_TOKEN", "").strip()


def enabled(ch: dict) -> bool:
    """A channel's MAX twin may send: switched on, a chat, a subscribe link and a token in the environment."""
    return bool(ch.get("max_enabled") and str(ch.get("max_chat") or "").strip() and (ch.get("max_link") or "").strip()
                and token_of(ch))


def _ctx() -> ssl.SSLContext:
    ca = config.env("CHANNELS_MAX_CA")
    return ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=_ctx()))


def call(token: str, method: str, path: str, params: dict | None = None, body: Any = None,
         timeout: float = 60) -> Any:
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": token, "Content-Type": "application/json"})
    try:
        with _opener().open(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise MaxError(f"MAX {method} {path}: HTTP {e.code} {e.read()[:300].decode('utf-8', 'replace')}") from None
    except (urllib.error.URLError, OSError) as e:
        raise MaxError(f"MAX {method} {path}: {e}") from None
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {"raw": raw.decode("utf-8", "replace")[:300]}


# -- text ---------------------------------------------------------------------------------------------------------------
def cta_html(ch: dict) -> str:
    label = ch.get("max_cta") or ch.get("cta") or ch.get("title") or "Подписаться"
    return f'👉 <a href="{htmlmod.escape(ch["max_link"].strip(), quote=True)}">{htmlmod.escape(label, quote=False)}</a>'


def text_for(post_html: str, ch: dict) -> str:
    """The Telegram post as MAX HTML (the same <b>, <i>, <a> tags): the subscribe line points to the MAX channel."""
    tg_cta = re.compile(r'👉 <a href="https://t\.me/[A-Za-z0-9_]+">[^<]*</a>\s*$')
    body = tg_cta.sub("", (post_html or "").rstrip()).rstrip()
    out = f"{body}\n\n{cta_html(ch)}" if body else cta_html(ch)
    if len(out) > MAX_TEXT:
        raise MaxError(f"текст длиннее {MAX_TEXT} знаков")
    return out


# -- media --------------------------------------------------------------------------------------------------------------
def _multipart(path: Path) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    ctype = "video/mp4" if path.suffix.lower() == ".mp4" else "image/jpeg"
    head = (f'--{b}\r\nContent-Disposition: form-data; name="data"; filename="{path.name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n").encode()
    return head + path.read_bytes() + f"\r\n--{b}--\r\n".encode(), f"multipart/form-data; boundary={b}"


def upload(token: str, path: Path, kind: str) -> dict:
    """One file → its attachment {"type": "image"|"video", "payload": {"token": …}}."""
    kind = "video" if kind == "video" else "image"
    slot = call(token, "POST", "/uploads", {"type": kind})
    url = slot.get("url")
    if not url:
        raise MaxError(f"MAX /uploads: нет url ({str(slot)[:200]})")
    body, ctype = _multipart(path)
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": ctype,
                                                                         "Authorization": token})
    try:
        with _opener().open(req, timeout=600) as r:
            raw = r.read()
    except (urllib.error.URLError, OSError) as e:
        raise MaxError(f"MAX upload: {e}") from None
    if kind == "video":
        tok = slot.get("token")
    else:
        tok = image_token(raw)
    if not tok:
        raise MaxError(f"MAX upload: нет токена ({raw[:200]!r})")
    return {"type": kind, "payload": {"token": tok}}


def image_token(raw: bytes) -> str | None:
    """photos.<photoId>.token of an image upload's answer."""
    try:
        photos = (json.loads(raw) or {}).get("photos") or {}
    except ValueError:
        return None
    for v in photos.values():
        if isinstance(v, dict) and v.get("token"):
            return v["token"]
    return None


# -- posting --------------------------------------------------------------------------------------------------------------
def send(ch: dict, post_html: str, media: list[dict], sleep=time.sleep) -> str:
    """The post into the channel's MAX twin; returns the message id (mid)."""
    token = token_of(ch)
    if not enabled(ch):
        raise MaxError("MAX выключен для этого канала (нет токена, чата, ссылки или флажка)")
    text = text_for(post_html, ch)
    atts = [upload(token, Path(m["path"]), m["kind"]) for m in media[:12]
            if m.get("path") and Path(m["path"]).is_file()]
    body = {"text": text, "format": "html", "notify": True}
    if atts:
        body["attachments"] = atts
    for k, wait in enumerate((0,) + NOT_READY_WAITS):
        if wait:
            sleep(wait)
        try:
            r = call(token, "POST", "/messages", {"chat_id": int(ch["max_chat"])}, body)
        except MaxError as e:
            if "attachment.not.ready" in str(e) and k < len(NOT_READY_WAITS):
                continue
            raise
        mid = ((r.get("message") or {}).get("body") or {}).get("mid")
        if not mid:
            raise MaxError(f"MAX: нет id сообщения в ответе ({str(r)[:200]})")
        return str(mid)
    raise MaxError("MAX: вложение так и не обработалось")


def delete(ch: dict, mid: str) -> None:
    r = call(token_of(ch), "DELETE", "/messages", {"message_id": mid})
    if not r.get("success", False):
        raise MaxError(f"MAX: не удалено ({r.get('message') or r})")


def check_rights(ch: dict) -> dict:
    """May the channel's MAX bot post (and delete) there? {"ok", "bot", "is_admin", "permissions", "detail"}."""
    token = token_of(ch)
    if not token:
        return {"ok": False, "detail": f"нет токена в {ch.get('max_bot_env') or 'MAX_BOT_TOKEN'}"}
    if not str(ch.get("max_chat") or "").strip().lstrip("-").isdigit():
        return {"ok": False, "detail": "не указан chat_id MAX-канала (число; приходит с событием bot_added)"}
    try:
        me = call(token, "GET", "/me")
        m = call(token, "GET", f"/chats/{int(ch['max_chat'])}/members/me")
    except MaxError as e:
        return {"ok": False, "detail": str(e)[:300]}
    perms = m.get("permissions") or []
    ok = bool(m.get("is_owner") or (m.get("is_admin") and "write" in perms))
    return {"ok": ok, "bot": me.get("username") or me.get("name"), "is_admin": m.get("is_admin"),
            "permissions": perms, "can_delete": bool(m.get("is_owner") or "delete" in perms),
            "detail": "" if ok else "бот не админ с правом публикации"}


def added_chats(token: str) -> list[dict]:
    """The chats the bot was added to, from the waiting bot_added updates (how MAX gives a channel's chat_id)."""
    r = call(token, "GET", "/updates", {"types": "bot_added", "limit": 100, "timeout": 0})
    out = []
    for u in r.get("updates") or []:
        if u.get("update_type") == "bot_added" and u.get("chat_id"):
            out.append({"chat_id": u["chat_id"], "is_channel": u.get("is_channel"), "user": (u.get("user") or {}).get("name")})
    return out
