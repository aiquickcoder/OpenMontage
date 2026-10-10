"""Minimal Telegram Bot API client (requests; SOCKS proxy for RU hosts via TG_PROXY)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import requests

API = "https://api.telegram.org/bot{token}/{method}"
VIDEO_LIMIT_BYTES = 49 * 1024 * 1024


class Telegram:
    def __init__(self, token: str, chat: str, proxy: str = "", session: Optional[requests.Session] = None):
        self.token, self.chat = token, str(chat)
        self.s = session or requests.Session()
        if proxy:
            self.s.proxies = {"https": proxy, "http": proxy}

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat)

    def call(self, method: str, *, http_timeout: float = 60, files: Any = None, **params: Any) -> Any:
        data = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                for k, v in params.items() if v is not None}
        resp = self.s.post(API.format(token=self.token, method=method), data=data, files=files,
                           timeout=http_timeout)
        payload = resp.json()
        if not payload.get("ok"):
            raise RuntimeError(f"telegram {method}: {payload.get('description')}")
        return payload["result"]

    def send(self, text: str, buttons: Optional[list[list[dict[str, str]]]] = None) -> None:
        if not self.enabled:
            return
        markup = {"inline_keyboard": buttons} if buttons else None
        self.call("sendMessage", chat_id=self.chat, text=text[:4000], reply_markup=markup,
                  disable_web_page_preview=True)

    def send_video(self, path: Path, caption: str, buttons: Optional[list[list[dict[str, str]]]] = None) -> None:
        if not self.enabled:
            return
        if path.stat().st_size > VIDEO_LIMIT_BYTES:
            self.send(caption + f"\n\n(видео {path.stat().st_size // 2**20} МБ — больше лимита Telegram)", buttons)
            return
        markup = {"inline_keyboard": buttons} if buttons else None
        with open(path, "rb") as f:
            self.call("sendVideo", http_timeout=600, files={"video": (path.name, f, "video/mp4")},
                      chat_id=self.chat, caption=caption[:1000], reply_markup=markup, supports_streaming=True)


def approval_buttons(brief_id: str) -> list[list[dict[str, str]]]:
    return [[{"text": "✅ В публикацию", "callback_data": f"approve:{brief_id}"},
             {"text": "❌ Отклонить", "callback_data": f"reject:{brief_id}"}]]
