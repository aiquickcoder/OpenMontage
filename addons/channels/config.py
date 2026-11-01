"""Settings of the channels engine. Paths and secrets come from the environment (/opt/openmontage/.env on the
server); everything the owner may want to change (models, frequencies, style guides) lives in the DB and the
Studio «Каналы» page, with the defaults below.

Bots. A channel row names the environment variable that holds its publishing bot's token (channels.bot_env,
default TG_TOKEN = the factory bot @content_luvael_bot): the token is never in the code or the DB. Reports to the
owner go through the factory bot (TG_TOKEN → TG_CHAT); its buttons are handled by factory-bot.service, the only
getUpdates reader of that token (factory/service.py → addons/channels/bot_actions.py).
"""
from __future__ import annotations

import os
from pathlib import Path


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).split(" #")[0].strip()


DATA = Path(env("CHANNELS_DATA_DIR") or (Path(env("FACTORY_DATA_DIR") or "/srv/factory") / "channels"))
DB_PATH = Path(env("CHANNELS_DB") or DATA / "channels.db")
MEDIA = DATA / "media"
LLM_URL = env("CHANNELS_LLM_URL", "https://openrouter.ai/api/v1/chat/completions")
LLM_KEY_ENV = env("CHANNELS_LLM_KEY_ENV", "OPENROUTER_API_KEY")
LLM_PROXY = env("CHANNELS_LLM_PROXY", "http://127.0.0.1:10809")  # OpenRouter answers 403 to a Russian IP
REPORT_TOKEN_ENV = "TG_TOKEN"
REPORT_CHAT_ENV = "TG_CHAT"
BOT_PROXY_ENV = "TG_PROXY"

# editable through settings (key -> default)
DEFAULTS: dict[str, object] = {
    "model_judge": "anthropic/claude-haiku-5.5",
    "model_write": "anthropic/claude-haiku-5.5",
    "model_check": "anthropic/claude-haiku-5.5",
    "model_vision": "google/gemini-3.8-flash",
    "daily_budget_usd": 5.0,           # the engine stops calling the AI for the day past it (and says so)
    "collect_every_min": 10,
    "story_window_h": 36,              # a new post joins a story at most this old
    "judge_min_posts": 1,
    "video_max_s": 90,                 # longer clips are not taken
    "inpaint_video_max_s": 25,         # a video that needs the cleaner (not a crop) only up to this length
    "crop_max_share": 0.18,            # a logo within this share of an edge is cropped off instead of inpainted
    "max_media": 4,                    # album size
}

CHANNELS: list[dict] = [  # seed rows; the numbers are replaced by research.freq results (see README)
    {"key": "news", "username": "zasnyali_news", "title": "ЗАСНЯЛИ Live", "regions": ["russia", "world", "moscow", "kazan"],
     "per_day": 40, "quiet_from": 1, "quiet_to": 7, "min_gap_min": 8, "min_score": 6.5, "max_age_h": 6,
     "cta": "ЗАСНЯЛИ Live — подписаться"},
    {"key": "moscow", "username": "zasnyali_moscow", "title": "ЗАСНЯЛИ МОСКВА Live", "regions": ["moscow"],
     "per_day": 20, "quiet_from": 1, "quiet_to": 7, "min_gap_min": 15, "min_score": 5.5, "max_age_h": 10,
     "cta": "ЗАСНЯЛИ МОСКВА — подписаться"},
    {"key": "kazan", "username": "zasnyali_kazan", "title": "ЗАСНЯЛИ КАЗАНЬ Live", "regions": ["kazan"],
     "per_day": 12, "quiet_from": 1, "quiet_to": 7, "min_gap_min": 20, "min_score": 5.0, "max_age_h": 14,
     "cta": "ЗАСНЯЛИ КАЗАНЬ — подписаться"},
]


def db():
    from addons.channels.db import DB

    return DB(DB_PATH)


def setting(d, key: str):
    return d.setting(key, DEFAULTS.get(key))
