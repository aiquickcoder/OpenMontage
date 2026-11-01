"""Hand controls of the channels engine (on the server, as the factory user, with /opt/openmontage/.env):

    .venv/bin/python -m addons.channels.cli status                 # channels, queue, services' heartbeat, AI cost
    .venv/bin/python -m addons.channels.cli check-bots             # may the channels' bots post? (getChatMember)
    .venv/bin/python -m addons.channels.cli mode news live|trial   # live only when its bot may post
    .venv/bin/python -m addons.channels.cli pause news | resume news
    .venv/bin/python -m addons.channels.cli posts [--status ready] [--n 10]   # the posts as they would appear
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

from addons.channels import config, publish, rewrite, schedule, seed


def status(db) -> None:
    now = datetime.now(schedule.MSK)
    for ch in db.channels():
        start = schedule.day_start(now).timestamp()
        counts = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM posts WHERE channel=? AND "
                                                     "updated>=? GROUP BY status", (ch["key"], start))}
        owed = schedule.owed(now, ch["per_day"], ch["hours"], ch["live_since"] or 0, ch["ramp"] or 0.5)
        print(f"@{ch['username']:<16} {ch['mode']:<5} {'ПАУЗА ' if ch['paused'] else ''}{ch['per_day']:g}/день, "
              f"к этому часу {owed}; сегодня: {json.dumps(counts, ensure_ascii=False)}")
    for k in ("collector_alive", "engine_alive", "media_alive", "publisher_alive"):
        t = db.setting(k)
        print(f"{k:<16} {('%.0f с назад' % (time.time() - t)) if t else 'не запускался'}")
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))
    for r in db.q("SELECT model, purpose, calls, tokens_in, tokens_out, cost FROM llm_usage WHERE day=?", (day,)):
        print(f"ИИ {r['purpose']:<7} {r['model']:<32} {r['calls']:>5} вызовов  ${r['cost']:.4f}")
    src = db.one("SELECT COUNT(*) n, SUM(joined) j FROM sources WHERE platform='telegram'")
    raw = db.one("SELECT COUNT(*) n FROM raw_posts WHERE read_at>=?", (time.time() - 3600,))
    print(f"источники: {src['n']} Telegram (вступил в {src['j'] or 0}), постов за час: {raw['n']}")
    for e in db.q("SELECT ts, kind, text FROM events ORDER BY id DESC LIMIT 8"):
        print(datetime.fromtimestamp(e["ts"], schedule.MSK).strftime("%H:%M"), e["kind"], e["text"][:150])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "check-bots", "mode", "pause", "resume", "posts", "seed"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--status", default="")
    ap.add_argument("--n", type=int, default=10)
    a = ap.parse_args()
    db = config.db()
    seed.seed(db)
    if a.cmd == "status":
        status(db)
    elif a.cmd == "check-bots":
        for ch in db.channels():
            print(ch["key"], json.dumps(publish.check_rights(ch), ensure_ascii=False))
    elif a.cmd == "mode":
        key, mode = a.args
        ch = db.channel(key)
        if mode == "live":
            r = publish.check_rights(ch)
            if not r.get("ok"):
                raise SystemExit(f"бот не может публиковать в @{ch['username']}: {r}")
            db.update("channels", "key", key, mode="live", chat_id=r["chat_id"], live_since=time.time(), note="")
        else:
            db.update("channels", "key", key, mode="trial")
        print(key, "→", mode)
    elif a.cmd in ("pause", "resume"):
        db.update("channels", "key", a.args[0], paused=int(a.cmd == "pause"))
        print(a.args[0], a.cmd)
    elif a.cmd == "posts":
        where = "WHERE status=?" if a.status else ""
        for p in db.q(f"SELECT * FROM posts {where} ORDER BY id DESC LIMIT ?", ([a.status] if a.status else []) + [a.n]):
            print(f"\n#{p['id']} {p['channel']} {p['status']} скор {p['score']} {'СРОЧНО ' if p['urgent'] else ''}"
                  f"{p['error'] or ''}\n{rewrite.plain(p['html'] or '')}\nмедиа: {p['media_note']}\n"
                  + "\n".join(p.get("sources") or []))


if __name__ == "__main__":
    main()
