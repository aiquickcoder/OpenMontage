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
    # Codex GPT by the ChatGPT subscription through codex-gateway (owner 2026-11-01); checked 2026-11-01: gpt-6.1-sol
    # and gpt-6.1-terra answer (terra faster), images go in as input_image; gpt-6.1-luna / -mini are refused
    "llm_provider": "codex",
    "model_judge": "gpt-6.1-terra",
    "model_write": "gpt-6.1-sol",
    "model_check": "gpt-6.1-terra",
    "model_vision": "gpt-6.1-sol",
    "model_vision2": "gpt-6.1-sol",   # a different second model doubles the looks (union of both); same = one look
    "model_fallback": "gpt-6.1-terra",  # used once when a call keeps failing (same provider)
    "codex_calls_per_day": 5000,       # cap on subscription calls a day
    "openrouter_fallback": False,      # OpenRouter only as an explicit fallback, off by default
    "model_openrouter": "anthropic/claude-haiku-5.5",
    "daily_budget_usd": 5.0,           # OpenRouter only: no paid calls past it for the day
    "collect_every_min": 10,
    "story_window_h": 36,              # a new post joins a story at most this old
    "judge_min_posts": 1,
    "video_max_s": 90,                 # longer clips are not taken
    "inpaint_video_max_s": 25,         # a video that needs the cleaner (not a crop) only up to this length
    "crop_max_share": 0.18,            # a logo within this share of an edge is cropped off instead of inpainted
    "max_media": 4,                    # album size
    "join_enabled": True,              # owner 2026-11-01: «Да разрешаю» — join the public source channels
    "join_every_s": 180,               # one join every ~3 minutes
    "max_channels": 480,               # the account's channel count stays under Telegram's 500
    "primary_bonus": 0.8,              # score bonus of a story that has a first-hand source (soft priority)
    "fresh_burst": 2,                  # a fresh strong story may run this many posts ahead of the schedule
    "fresh_min": 20,                   # «fresh» = first seen this many minutes ago at most
}

# Measured 2026-11-01 through Telethon (research.py freq, 14 full days 18–31.10, ads and reposts left out):
#   main  — Топор Live 67.1/day, Топор 12.6, Mash 16.6, Baza 16.1, SHOT 17.9, Readovka 23.1, Осторожно 19.6,
#           Раньше всех 78.6, Ньюсач 48.4, Varlamov 12.9 → mean 31.3
#   Moscow — Москвач 17.5, Новости Москвы 14.0, Москва сейчас 24.4, Осторожно Москва 11.9, МСК 4.9, Москва 24 52.0,
#           Мосфид 9.1, ЧП/Москва 17.6, ДТП и ЧП 5.5 → mean 17.4
#   Kazan — Казань на максималках 19.6, КазаньДА 16.4, Казань сейчас 2.0, Go Kazan 17.6, ЧП Казань 10.9,
#           Происшествия Татарстан 5.4, Mash Iptash 4.9, Казань Сейчас 15.1, Первый Казанский 22.3, ТАТМЕДИА 22.7,
#           ЧП Казань новости 13.9 → mean 13.7
# Hours: the references' average posts by hour (MSK), quiet hours set to 0 (urgent posts still go).
HOURS = {
    "news": [0.41, 0, 0, 0, 0, 0, 0, 0.36, 1.22, 1.61, 1.67, 1.66, 1.7, 1.91, 1.65, 1.58, 1.7, 1.51, 1.3, 1.29, 1.29,
             1.24, 1.16, 0.74],
    "moscow": [0.29, 0, 0, 0, 0, 0, 0, 0.34, 0.82, 1.71, 1.45, 1.57, 1.83, 1.9, 1.51, 1.91, 1.65, 1.99, 1.78, 1.6, 1.17,
               0.99, 0.83, 0.65],
    "kazan": [0, 0, 0, 0, 0, 0, 0, 0.64, 1.53, 2.06, 2.15, 1.49, 2.06, 1.56, 2.07, 1.57, 1.42, 1.66, 1.45, 1.16, 0.99,
              1.15, 0.51, 0.51],
}
CHANNELS: list[dict] = [
    {"key": "news", "username": "zasnyali_news", "title": "ЗАСНЯЛИ Live", "regions": ["russia", "world", "moscow", "kazan"],
     "per_day": 31, "hours": HOURS["news"], "quiet_from": 1, "quiet_to": 7, "min_gap_min": 8, "min_score": 6.0,
     "max_age_h": 6, "cta": "ЗАСНЯЛИ Live. Подписаться"},
    {"key": "moscow", "username": "zasnyali_moscow", "title": "ЗАСНЯЛИ МОСКВА Live", "regions": ["moscow"],
     "per_day": 17, "hours": HOURS["moscow"], "quiet_from": 1, "quiet_to": 7, "min_gap_min": 15, "min_score": 5.0,
     "max_age_h": 10, "cta": "ЗАСНЯЛИ МОСКВА. Подписаться"},
    {"key": "kazan", "username": "zasnyali_kazan", "title": "ЗАСНЯЛИ КАЗАНЬ Live", "regions": ["kazan"],
     "per_day": 14, "hours": HOURS["kazan"], "quiet_from": 0, "quiet_to": 7, "min_gap_min": 20, "min_score": 4.5,
     "max_age_h": 14, "cta": "ЗАСНЯЛИ КАЗАНЬ. Подписаться"},
]


def db():
    from addons.channels.db import DB

    return DB(DB_PATH)


def setting(d, key: str):
    return d.setting(key, DEFAULTS.get(key))
