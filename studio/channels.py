"""«Каналы» — the ЗАСНЯЛИ news channels on the Studio dashboard (behind the owner's Studio login).

Reads and edits the channels engine's DB (addons/channels; FACTORY_DATA_DIR/channels/channels.db): channels (rate,
hours, pause, trial/live), the queue and history of posts (delete / take off), stories with the AI's verdicts,
sources (add / remove / weight), style guides, prompts and models, and the AI spend.

Wiring (studio/app.py): `app.include_router(channels.router)`.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from studio import auth

router = APIRouter(prefix="/api/channels", dependencies=[Depends(auth.require_user)])
POST_STATES = ("draft", "media", "ready", "previewed", "published", "deleted", "rejected", "expired", "failed")
SETTING_KEYS = ("style_common", "style_news", "style_city", "model_judge", "model_write", "model_check", "model_vision",
                "model_vision2", "daily_budget_usd", "video_max_s", "inpaint_video_max_s", "crop_max_share",
                "max_media", "join_enabled", "join_every_s", "max_channels")


def _db():
    from addons.channels import config, seed

    d = config.db()
    seed.seed(d)
    return d


class ChannelPatch(BaseModel):
    per_day: Optional[float] = Field(default=None, ge=0, le=300)
    quiet_from: Optional[int] = Field(default=None, ge=0, le=23)
    quiet_to: Optional[int] = Field(default=None, ge=0, le=23)
    min_gap_min: Optional[int] = Field(default=None, ge=1, le=600)
    min_score: Optional[float] = Field(default=None, ge=0, le=20)
    max_age_h: Optional[float] = Field(default=None, ge=1, le=72)
    paused: Optional[bool] = None
    mode: Optional[str] = Field(default=None, pattern="^(trial|live)$")
    cta: Optional[str] = Field(default=None, max_length=80)
    style: Optional[str] = Field(default=None, max_length=3000)
    hours: Optional[list[float]] = Field(default=None, min_length=24, max_length=24)
    bot_env: Optional[str] = Field(default=None, pattern="^[A-Z][A-Z0-9_]{2,60}$")


class SourceIn(BaseModel):
    platform: str = Field(pattern="^(telegram|rss|site|rutube|youtube)$")
    ref: str = Field(min_length=2, max_length=300)
    title: str = Field(default="", max_length=200)
    region: str = Field(default="russia", pattern="^(russia|moscow|kazan|world)$")
    kind: str = Field(default="aggregator", pattern="^(aggregator|city|primary|media)$")
    trust: float = Field(default=0.6, ge=0, le=1)
    every_min: int = Field(default=10, ge=1, le=1440)


class SourcePatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    region: Optional[str] = Field(default=None, pattern="^(russia|moscow|kazan|world)$")
    kind: Optional[str] = Field(default=None, pattern="^(aggregator|city|primary|media)$")
    trust: Optional[float] = Field(default=None, ge=0, le=1)
    every_min: Optional[int] = Field(default=None, ge=1, le=1440)
    enabled: Optional[bool] = None


class SettingsIn(BaseModel):
    values: dict[str, Any]


def _alive(d) -> dict[str, Optional[float]]:
    out = {}
    for k in ("collector", "engine", "media", "publisher"):
        t = d.setting(f"{k}_alive")
        out[k] = round(time.time() - t) if t else None
    return out


@router.get("/overview")
def overview() -> dict:
    from addons.channels import llm, schedule

    d = _db()
    now = datetime.now(schedule.MSK)
    start = schedule.day_start(now).timestamp()
    chans = []
    for ch in d.channels():
        counts = {r["status"]: r["n"] for r in d.q("SELECT status, COUNT(*) n FROM posts WHERE channel=? AND "
                                                    "updated>=? GROUP BY status", (ch["key"], start))}
        queue = d.one("SELECT COUNT(*) n FROM posts WHERE channel=? AND status IN ('draft','media','ready')",
                      (ch["key"],))["n"]
        out_today = d.one("SELECT COUNT(*) n FROM posts WHERE channel=? AND status IN ('published','previewed',"
                          "'deleted') AND published_at>=?", (ch["key"], start))["n"]
        owed = schedule.owed(now, ch["per_day"], ch["hours"] or [], ch["live_since"] or 0, ch["ramp"] or 0.5)
        chans.append({**ch, "today": counts, "queue": queue, "out_today": out_today, "owed_now": owed,
                      "rights": d.setting(f"rights:{ch['key']}") or {}})
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))
    usage = d.q("SELECT model, purpose, calls, tokens_in, tokens_out, cost FROM llm_usage WHERE day=?", (day,))
    src = d.one("SELECT COUNT(*) n, SUM(enabled) e, SUM(joined) j FROM sources")
    posts_hour = d.one("SELECT COUNT(*) n FROM raw_posts WHERE read_at>=?", (time.time() - 3600,))["n"]
    events = d.q("SELECT ts, kind, text FROM events ORDER BY id DESC LIMIT 30")
    return {"channels": chans, "usage": usage, "spent_today": round(llm.spent_today(d), 4),
            "budget": d.setting("daily_budget_usd", 5.0), "alive": _alive(d), "sources": src,
            "posts_last_hour": posts_hour, "reader_channels": d.setting("reader_channels"), "events": events}


@router.patch("/ch/{key}")
def patch_channel(key: str, body: ChannelPatch) -> dict:
    d = _db()
    ch = d.channel(key)
    if not ch:
        raise HTTPException(404, "нет такого канала")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "paused" in fields:
        fields["paused"] = int(fields["paused"])
    if fields.get("mode") == "live" and ch["mode"] != "live":
        from addons.channels import publish

        r = publish.check_rights({**ch, **fields})
        d.set_setting(f"rights:{key}", {**r, "ts": time.time()})
        if not r.get("ok"):
            raise HTTPException(409, f"бот не может публиковать в @{ch['username']}: {r.get('detail') or r.get('status')}")
        fields.update(chat_id=r["chat_id"], live_since=time.time(), note="")
    d.update("channels", "key", key, **fields)
    d.event("control", f"{key}: {', '.join(f'{k}={v}' for k, v in fields.items() if k not in ('style', 'hours'))} (веб)"[:300])
    return d.channel(key)


@router.get("/posts")
def posts(status: str = "", channel: str = "", limit: int = 60) -> list[dict]:
    d = _db()
    where, args = [], []
    if status:
        st = [s for s in status.split(",") if s in POST_STATES]
        where.append(f"status IN ({','.join('?' * len(st))})")
        args += st
    if channel:
        where.append("channel=?")
        args.append(channel)
    sql = "SELECT * FROM posts" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    rows = d.q(sql, args + [min(limit, 300)])
    for r in rows:
        r["media_count"] = len(r.get("media") or [])
        r["media"] = [{"kind": m.get("kind"), "how": m.get("how")} for m in r.get("media") or []]
    return rows


@router.post("/posts/{post_id}/delete")
def delete_post(post_id: int) -> dict:
    from addons.channels import bot_actions

    note, _ = bot_actions.handle("chdel", str(post_id))
    return {"note": note}


@router.get("/posts/{post_id}/media/{n}")
def post_media(post_id: int, n: int) -> FileResponse:
    from addons.channels import config

    d = _db()
    p = d.one("SELECT media FROM posts WHERE id=?", (post_id,))
    items = (p or {}).get("media") or []
    if n >= len(items):
        raise HTTPException(404, "нет медиа")
    path = Path(items[n]["path"]).resolve()
    if not path.is_file() or not path.is_relative_to(config.MEDIA.resolve()):
        raise HTTPException(404, "файл удалён")
    return FileResponse(path)


@router.get("/stories")
def stories(limit: int = 80, status: str = "") -> list[dict]:
    d = _db()
    where = "WHERE status=?" if status else "WHERE status NOT IN ('new','merged')"
    rows = d.q(f"SELECT id, created, first_ts, title, lead, region, category, n_posts, n_channels, max_views, "
               f"has_primary, status, score, reason FROM stories {where} ORDER BY id DESC LIMIT ?",
               ([status] if status else []) + [min(limit, 300)])
    return rows


@router.get("/sources")
def sources() -> list[dict]:
    d = _db()
    return d.q("SELECT id, platform, ref, title, region, kind, trust, enabled, every_min, joined, last_read, "
               "posts_seen, last_post_ts, error, note FROM sources ORDER BY kind, region, trust DESC")


@router.post("/sources", status_code=201)
def add_source(body: SourceIn) -> dict:
    d = _db()
    ref = body.ref.strip()
    if body.platform == "telegram":
        ref = ref.removeprefix("https://t.me/").removeprefix("t.me/").lstrip("@").split("/")[0]
    sid = d.insert("sources", {**body.model_dump(), "ref": ref, "added": time.time()}, ignore=True)
    if not sid:
        raise HTTPException(409, "такой источник уже есть")
    d.event("control", f"добавлен источник {body.platform}:{ref} (веб)")
    return {"id": sid}


@router.patch("/sources/{sid}")
def patch_source(sid: int, body: SourcePatch) -> dict:
    d = _db()
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])
    d.update("sources", "id", sid, **fields)
    return d.one("SELECT * FROM sources WHERE id=?", (sid,)) or {}


@router.delete("/sources/{sid}")
def delete_source(sid: int) -> dict:
    d = _db()
    s = d.one("SELECT platform, ref FROM sources WHERE id=?", (sid,))
    d.x("DELETE FROM sources WHERE id=?", (sid,))
    if s:
        d.event("control", f"удалён источник {s['platform']}:{s['ref']} (веб)")
    return {"ok": True}


@router.get("/settings")
def get_settings() -> dict:
    from addons.channels import config, style

    d = _db()
    defaults = {**config.DEFAULTS, "style_common": style.COMMON, "style_news": style.NEWS, "style_city": style.CITY}
    return {k: d.setting(k, defaults.get(k)) for k in SETTING_KEYS}


@router.put("/settings")
def put_settings(body: SettingsIn) -> dict:
    d = _db()
    bad = [k for k in body.values if k not in SETTING_KEYS]
    if bad:
        raise HTTPException(422, f"неизвестные настройки: {', '.join(bad)}")
    for k, v in body.values.items():
        d.set_setting(k, v)
    d.event("control", f"настройки изменены: {', '.join(body.values)} (веб)"[:300])
    return get_settings()
