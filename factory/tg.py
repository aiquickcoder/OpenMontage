"""Minimal Telegram Bot API client (requests; SOCKS proxy for RU hosts via TG_PROXY)."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

import requests

API = "https://api.telegram.org/bot{token}/{method}"
TOKEN_IN_TEXT = re.compile(r"bot\d+:[A-Za-z0-9_-]+")


def mask(text: str, token: str = "") -> str:
    """A text with the bot token taken out: requests puts the whole URL — /bot<token>/getUpdates — into its errors,
    and the bot loop logs them (2026-11-01, an SSL error printed the token into the journal)."""
    if token:
        text = text.replace(token, "***")
    return TOKEN_IN_TEXT.sub("bot***", text)
VIDEO_LIMIT_BYTES = 49 * 1024 * 1024
FIT_BYTES = 46 * 1024 * 1024  # what a squeezed copy aims at: under the limit, with room for x264 overshoot


def video_meta(path: Path) -> dict[str, int]:
    """width/height/duration for sendVideo; without them Telegram guesses a square preview."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=True).stdout
        info = json.loads(out)
        stream = info["streams"][0]
        return {"width": int(stream["width"]), "height": int(stream["height"]),
                "duration": round(float(info["format"]["duration"]))}
    except Exception:
        return {}


def fit_kbps(duration: float, audio_kbps: int = 128, target: int = FIT_BYTES) -> int:
    """Video kbit/s that keeps a film of this length under the bot's upload limit."""
    return max(300, int(target * 8 / 1000 / max(duration, 1)) - audio_kbps)


def squeeze(path: Path) -> Path:
    """A copy of an mp4 that fits the bot's 50 MB: same picture size, two-pass x264 at the bitrate that fits
    (owner 2026-11-01: «файлом в бот не отдаётся» — the films grew to 60–210 MB and only a note went out)."""
    out = path.with_name(path.stem + ".tg.mp4")
    if out.is_file() and out.stat().st_mtime >= path.stat().st_mtime:  # the second chat gets the same copy
        return out
    kbps = fit_kbps(video_meta(path).get("duration") or 60)
    with tempfile.TemporaryDirectory() as tmp:
        x264 = ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-c:v", "libx264", "-preset", "medium",
                "-b:v", f"{kbps}k", "-maxrate", f"{kbps * 3 // 2}k", "-bufsize", f"{kbps * 2}k",
                "-passlogfile", str(Path(tmp) / "pass")]
        subprocess.run(x264 + ["-pass", "1", "-an", "-f", "null", "-"], check=True, timeout=3600)
        subprocess.run(x264 + ["-pass", "2", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)],
                       check=True, timeout=3600)
    return out


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
        try:
            resp = self.s.post(API.format(token=self.token, method=method), data=data, files=files,
                               timeout=http_timeout)
        except requests.RequestException as exc:  # its message carries the URL with the token
            raise RuntimeError(f"telegram {method}: {mask(f'{type(exc).__name__}: {exc}', self.token)}") from None
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
                      chat_id=self.chat, caption=caption[:1000], reply_markup=markup, supports_streaming=True,
                      **video_meta(path))

    def send_document(self, path: Path, caption: str, name: str = "") -> None:
        """A file as it is: Telegram neither recompresses it nor turns an mp4 into a video message."""
        if not self.enabled:
            return
        note = ""
        if path.stat().st_size > VIDEO_LIMIT_BYTES:
            full = path.stat().st_size // 2**20
            path = squeeze(path)
            if path.stat().st_size > VIDEO_LIMIT_BYTES:
                self.send(caption + f"\n\n(файл {full} МБ — больше лимита Telegram)")
                return
            note = f"\n\n(файл сжат с {full} до {path.stat().st_size // 2**20} МБ — лимит бота 50 МБ)"
        with open(path, "rb") as f:
            self.call("sendDocument", http_timeout=600, files={"document": (name or path.name, f, "video/mp4")},
                      chat_id=self.chat, caption=caption[:1000 - len(note)] + note, disable_content_type_detection=True)


def approval_buttons(brief_id: str) -> list[list[dict[str, str]]]:
    return [[{"text": "✅ В публикацию", "callback_data": f"approve:{brief_id}"},
             {"text": "❌ Отклонить", "callback_data": f"reject:{brief_id}"}]]
