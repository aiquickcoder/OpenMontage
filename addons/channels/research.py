"""Groundwork for the ЗАСНЯЛИ channels, read from Telegram itself (Telethon, our session copy — tgread.py).

    .venv/bin/python -m addons.channels.research resolve toporlive,mash,...        # which usernames exist, titles
    .venv/bin/python -m addons.channels.research freq toporlive,mash --days 14 [--json out.json]
    .venv/bin/python -m addons.channels.research dump toporlive --n 100 --json out.json   # style samples
    .venv/bin/python -m addons.channels.research search "Москва сейчас,Казань новости"      # find public channels

freq: posts a day and by hour of the day (MSK) over the last --days, counting an album once and leaving out ads
(filters.is_ad) and reposts (fwd_from) — what a channel itself publishes as news. The days at the edges are partial
and are not averaged.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from addons.channels import filters, tgread

MSK = tgread.MSK


async def resolve(names: list[str]) -> list[dict]:
    from telethon.tl.functions.channels import GetFullChannelRequest

    out = []
    async with tgread.session() as c:
        for n in names:
            try:
                e = await c.get_entity(n)
                full = await c(GetFullChannelRequest(e))
                out.append({"username": n, "title": getattr(e, "title", ""), "id": e.id,
                            "subscribers": full.full_chat.participants_count,
                            "broadcast": bool(getattr(e, "broadcast", False))})
            except Exception as ex:  # noqa: BLE001 — a wrong guess is just reported
                out.append({"username": n, "error": f"{type(ex).__name__}: {ex}"[:160]})
            await asyncio.sleep(2.5)
    return out


async def search(queries: list[str]) -> list[dict]:
    """Telegram's global search for public channels (contacts.Search): username, title, subscribers."""
    from telethon.tl.functions.contacts import SearchRequest

    out = []
    async with tgread.session() as c:
        for q in queries:
            try:
                res = await c(SearchRequest(q=q, limit=20))
                for ch in res.chats:
                    if getattr(ch, "broadcast", False) and getattr(ch, "username", None):
                        out.append({"q": q, "username": ch.username, "title": ch.title,
                                    "subscribers": getattr(ch, "participants_count", None)})
            except Exception as ex:  # noqa: BLE001
                out.append({"q": q, "error": f"{type(ex).__name__}: {ex}"[:160]})
            await asyncio.sleep(3)
    return out


async def history(c, name: str, days: float, cap: int = 6000) -> list:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    msgs = []
    async for m in c.iter_messages(name, limit=cap):
        if m.date < since:
            break
        msgs.append(m)
    return msgs


def frequency(msgs: list, days: float, now: datetime | None = None) -> dict:
    """Posts per full day and the average by hour (MSK) of a channel's messages."""
    now = now or datetime.now(timezone.utc)
    seen_groups: set[int] = set()
    kept, ads, fwd, total = [], 0, 0, 0
    for m in msgs:
        if getattr(m, "action", None):
            continue
        if m.grouped_id:
            if m.grouped_id in seen_groups:
                continue
            seen_groups.add(m.grouped_id)
        total += 1
        text = m.message or ""
        if m.grouped_id and not text:  # the album's caption may sit on another item
            pass
        if getattr(m, "fwd_from", None):
            fwd += 1
            continue
        if filters.is_ad(text):
            ads += 1
            continue
        kept.append(m.date.astimezone(MSK))
    today = now.astimezone(MSK).date()
    first = today - timedelta(days=int(days))
    full_days = [first + timedelta(days=i) for i in range(1, int(days))]  # edges are partial
    per_day = Counter(d.date() for d in kept)
    counts = [per_day.get(d, 0) for d in full_days]
    by_hour = Counter(d.hour for d in kept if d.date() in set(full_days))
    n_days = max(1, len(full_days))
    return {"days": len(full_days), "posts_per_day": round(sum(counts) / n_days, 1),
            "min_day": min(counts) if counts else 0, "max_day": max(counts) if counts else 0,
            "per_day": {d.isoformat(): per_day.get(d, 0) for d in full_days},
            "by_hour": [round(by_hour.get(h, 0) / n_days, 2) for h in range(24)],
            "share_ads": round(ads / max(1, total), 3), "share_reposts": round(fwd / max(1, total), 3),
            "total_seen": total}


async def freq(names: list[str], days: float) -> dict:
    out = {}
    async with tgread.session() as c:
        for n in names:
            try:
                msgs = await history(c, n, days + 1)
                out[n] = frequency(msgs, days + 1)
            except Exception as ex:  # noqa: BLE001
                out[n] = {"error": f"{type(ex).__name__}: {ex}"[:160]}
            print(n, json.dumps({k: v for k, v in out[n].items() if k not in ("per_day", "by_hour")},
                                ensure_ascii=False), flush=True)
            await asyncio.sleep(2)
    return out


async def dump(name: str, n: int) -> list[dict]:
    rows = []
    async with tgread.session() as c:
        async for m in c.iter_messages(name, limit=n * 2):
            if getattr(m, "action", None) or not (m.text or "").strip():
                continue
            rows.append({"post": m.id, "date": m.date.astimezone(MSK).isoformat(timespec="minutes"),
                         "views": m.views or 0, "media": tgread.media_kind(m), "fwd": bool(m.fwd_from),
                         "ad": filters.is_ad(m.message or ""), "text": m.text})
            if len(rows) >= n:
                break
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["resolve", "freq", "dump", "search"])
    ap.add_argument("names")
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    names = [x.strip().lstrip("@") for x in a.names.split(",") if x.strip()]
    if a.cmd == "resolve":
        res = asyncio.run(resolve(names))
        for r in res:
            print(json.dumps(r, ensure_ascii=False), flush=True)
    elif a.cmd == "search":
        res = asyncio.run(search([x.strip() for x in a.names.split(",") if x.strip()]))
        for r in res:
            print(json.dumps(r, ensure_ascii=False), flush=True)
    elif a.cmd == "freq":
        res = asyncio.run(freq(names, a.days))
    else:
        res = asyncio.run(dump(names[0], a.n))
        print(f"{len(res)} posts")
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
