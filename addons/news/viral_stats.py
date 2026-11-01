"""The TikTok numbers of our viral films and the weekly tuning of the pick's weights (owner 2026-11-01: «учиться на
своих цифрах»). There is no access to TikTok's statistics yet, so the owner types them in the factory bot; automatic
collection comes later.

    # the factory bot (factory/service.py runs `viral_stats.py bot "<message>"` for every /tt message):
    /tt                                          — the films waiting for their numbers, and the format
    /tt a-02-11-1 15400 41% 230 96 [1200]        — views, watched to the end, comments, reposts [, likes]
    /tt веса                                     — the weights now and the last tuning
    # the server (timer news-viral-tune, Monday 05:30 MSK), or by hand:
    .venv/bin/python addons/news/viral_stats.py tune [--send] [--dry-run]
    .venv/bin/python addons/news/viral_stats.py register projects/news-viral-a-02-11-1     # morning.py does it
    .venv/bin/python addons/news/viral_stats.py add a-02-11-1 15400 41 230 96

A film's numbers are best a day after it went out («через сутки»); a later entry of the same film replaces it in the
tuning (the last one counts). The store is <viral data>/stats.db (SQLite): films (what the pick saw — the features
and signals of the story the film was made of), stats (every entry, kept), tune_log (every tuning and what changed).

The tuning (`tune`), simple on purpose:
- each film's result y = lg(views) + the share watched to the end + ½·lg(1 + comments per 1000 views)
  + ½·lg(1 + reposts per 1000 views);
- for every feature of the score (viral.WEIGHTS), its correlation with y over the films of the last 28 days that have
  numbers; with fewer than MIN_FILMS films nothing changes;
- weight × (1 + 0.25 × correlation), the step at most ±15 %, the weight kept within 0.4–2.5× its default, then all
  weights scaled back to 100 points; every change is logged with its reason and sent to the bot.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))
import viral  # noqa: E402

MSK = timezone(timedelta(hours=3))
MIN_FILMS = 6
STEP, MAX_STEP, LOW, HIGH = 0.25, 0.15, 0.4, 2.5
SCHEMA = """
CREATE TABLE IF NOT EXISTS films(slug TEXT PRIMARY KEY, account TEXT, day TEXT, key TEXT, title TEXT, score REAL,
  features TEXT, created REAL);
CREATE TABLE IF NOT EXISTS stats(id INTEGER PRIMARY KEY, slug TEXT, ts REAL, views INTEGER, completion REAL,
  comments INTEGER, shares INTEGER, likes INTEGER, raw TEXT);
CREATE TABLE IF NOT EXISTS tune_log(id INTEGER PRIMARY KEY, ts REAL, films INTEGER, changes TEXT, text TEXT);
"""


def db(path: Path | None = None) -> sqlite3.Connection:
    path = path or viral.DATA / "stats.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


# -- films ------------------------------------------------------------------------------------------------------------
def short(slug: str) -> str:
    return slug.removeprefix("news-viral-")


def resolve(con: sqlite3.Connection, name: str, root: Path = REPO) -> str | None:
    """A film by its slug or short id («a-02-11-1»); registered on the fly when its project is there."""
    name = name.strip().removeprefix("projects/").strip("/")
    slug = name if name.startswith("news-") else f"news-viral-{name}"
    if con.execute("SELECT 1 FROM films WHERE slug=?", (slug,)).fetchone():
        return slug
    if (root / "projects" / slug / "story.json").is_file():
        register(con, root / "projects" / slug)
        return slug
    return None


def register(con: sqlite3.Connection, project: Path) -> dict:
    """A delivered film into the store, with the features and signals the pick gave its story (viral.json)."""
    story = json.loads((project / "story.json").read_text(encoding="utf-8"))
    try:
        pick_ = json.loads((project / "viral.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pick_ = {}
    v = story.get("viral") or {}
    key = (story.get("source") or {}).get("key") or v.get("key")
    links = set((story.get("source") or {}).get("links") or [])
    cand = next((c for c in pick_.get("candidates", []) if c.get("key") == key or links & set(c.get("links") or [])),
                None)
    feats = {**(cand or {}).get("features", {}), **(cand or {}).get("signal_features", {})}
    m = re.match(r"news-viral-([a-z0-9]+)-(\d\d-\d\d)-\d+$", project.name)
    row = {"slug": project.name, "account": v.get("account") or (m.group(1) if m else pick_.get("account") or ""),
           "day": m.group(2) if m else "", "key": key or "", "title": story.get("headline") or story.get("hook") or "",
           "score": (cand or {}).get("score"), "features": json.dumps(feats, ensure_ascii=False),
           "created": (project / "story.json").stat().st_mtime}
    con.execute("INSERT INTO films(slug, account, day, key, title, score, features, created) "
                "VALUES(:slug, :account, :day, :key, :title, :score, :features, :created) "
                "ON CONFLICT(slug) DO UPDATE SET account=excluded.account, key=excluded.key, title=excluded.title, "
                "score=excluded.score, features=excluded.features", row)
    con.commit()
    return row


# -- numbers ----------------------------------------------------------------------------------------------------------
def number(s: str) -> float:
    """«15400», «15 400», «15,4k», «15.4к», «1.2м», «41%» → a number (a share for %: 41% → 41)."""
    t = s.strip().lower().replace(" ", "").replace(" ", "").rstrip("%").replace(",", ".")
    mult = 1.0
    if t.endswith(("k", "к", "тыс")):
        mult, t = 1000.0, re.sub(r"(?:k|к|тыс)$", "", t)
    elif t.endswith(("m", "м", "млн")):
        mult, t = 1e6, re.sub(r"(?:m|м|млн)$", "", t)
    return float(t) * mult


def parse(args: list[str]) -> dict:
    """views, completion (% or share), comments, shares [, likes]."""
    if len(args) < 4:
        raise ValueError("нужно 4 числа: просмотры, досмотры %, комментарии, репосты (и по желанию лайки)")
    views, comp, comments, shares = (number(x) for x in args[:4])
    likes = number(args[4]) if len(args) > 4 else None
    completion = comp / 100 if comp > 1 else comp
    if not 0 <= completion <= 1:
        raise ValueError("досмотры — доля от 0 до 100 %")
    if views <= 0:
        raise ValueError("просмотров должно быть больше нуля")
    return {"views": int(views), "completion": round(completion, 4), "comments": int(comments), "shares": int(shares),
            "likes": int(likes) if likes is not None else None}


def add(con: sqlite3.Connection, slug: str, nums: dict, raw: str = "") -> None:
    con.execute("INSERT INTO stats(slug, ts, views, completion, comments, shares, likes, raw) VALUES(?,?,?,?,?,?,?,?)",
                (slug, time.time(), nums["views"], nums["completion"], nums["comments"], nums["shares"],
                 nums.get("likes"), raw[:300]))
    con.commit()


def result(s: dict) -> float:
    """A film's result: lg(views) + the share watched to the end + ½·lg(1 + comments per 1000 views)
    + ½·lg(1 + reposts per 1000 views)."""
    v = max(1, s["views"])
    return (math.log10(v) + float(s["completion"] or 0) + 0.5 * math.log10(1 + 1000 * s["comments"] / v)
            + 0.5 * math.log10(1 + 1000 * s["shares"] / v))


def latest(con: sqlite3.Connection, days: float = 28) -> list[dict]:
    """Every film of the last days with numbers: its features and its last entry."""
    rows = con.execute(
        "SELECT f.slug, f.account, f.features, f.score, s.views, s.completion, s.comments, s.shares, s.likes, s.ts "
        "FROM films f JOIN stats s ON s.slug = f.slug WHERE s.id = (SELECT MAX(id) FROM stats WHERE slug = f.slug) "
        "AND f.created >= ?", (time.time() - days * 86400,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["features"] = json.loads(d["features"] or "{}")
        out.append(d)
    return out


# -- tuning -----------------------------------------------------------------------------------------------------------
def pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3 or statistics.pstdev(xs) == 0 or statistics.pstdev(ys) == 0:
        return 0.0
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))


def tuned(weights: dict[str, float], films: list[dict], defaults: dict[str, float] | None = None
          ) -> tuple[dict[str, float], list[dict]]:
    """New weights from the films' numbers and the list of changes (feature, old, new, correlation, films)."""
    defaults = defaults or viral.WEIGHTS
    ys = [result(f) for f in films]
    raw, changes = {}, []
    for k, w in weights.items():
        pairs = [(float(f["features"][k]) / (10 if k in viral.AI_FEATURES else 1), y)
                 for f, y in zip(films, ys) if k in f["features"]]
        r = pearson([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= MIN_FILMS else 0.0
        step = max(-MAX_STEP, min(MAX_STEP, STEP * r))
        raw[k] = min(defaults[k] * HIGH, max(defaults[k] * LOW, w * (1 + step)))
        changes.append({"feature": k, "old": w, "corr": round(r, 2), "films": len(pairs)})
    total = sum(raw.values())
    new = {k: round(v * 100 / total, 2) for k, v in raw.items()}
    for c in changes:
        c["new"] = new[c["feature"]]
    return new, changes


NAMES = {"everyone": "касается каждого", "emotion": "эмоция", "debate": "спор", "visual": "живые кадры",
         "explain": "объяснимо", "velocity": "разгон", "pickup": "подхват каналами", "reach": "охват",
         "engage": "комментарии/реакции"}


def tune(con: sqlite3.Connection, dry: bool = False) -> str:
    films = latest(con)
    weights = viral.load_weights()
    if len(films) < MIN_FILMS:
        text = (f"Подстройка весов: роликов с цифрами за 28 дней — {len(films)}, нужно хотя бы {MIN_FILMS}. "
                "Веса не трогаю. Цифры вводятся в боте: /tt")
        con.execute("INSERT INTO tune_log(ts, films, changes, text) VALUES(?,?,?,?)", (time.time(), len(films), "[]", text))
        con.commit()
        return text
    new, changes = tuned(weights, films)
    lines = [f"Подстройка весов по {len(films)} роликам за 28 дней (результат = lg просмотров + досмотры + "
             "½·lg комментариев и ½·lg репостов на 1000 просмотров):"]
    for c in sorted(changes, key=lambda c: -abs(c["new"] - c["old"])):
        d = c["new"] - c["old"]
        mark = "↑" if d > 0.05 else "↓" if d < -0.05 else "="
        lines.append(f"{mark} {NAMES.get(c['feature'], c['feature'])}: {c['old']:.1f} → {c['new']:.1f} "
                     f"(связь с успехом {c['corr']:+.2f}, роликов {c['films']})")
    best = max(films, key=result)
    lines.append(f"Лучший ролик: {short(best['slug'])} — {best['views']:,} просмотров, досмотры "
                 f"{best['completion'] * 100:.0f} %.")
    text = "\n".join(lines) + ("\n(пробно: веса не записаны)" if dry else "")
    if not dry:
        viral.DATA.mkdir(parents=True, exist_ok=True)
        (viral.DATA / "weights.json").write_text(json.dumps(
            {"weights": new, "updated": datetime.now(MSK).isoformat(timespec="seconds"), "films": len(films)},
            ensure_ascii=False, indent=1), encoding="utf-8")
        con.execute("INSERT INTO tune_log(ts, films, changes, text) VALUES(?,?,?,?)",
                    (time.time(), len(films), json.dumps(changes, ensure_ascii=False), text))
        con.commit()
    return text


# -- the bot ----------------------------------------------------------------------------------------------------------
HELP = ("Цифры роликов TikTok (через сутки после выкладки):\n"
        "/tt <ролик> <просмотры> <досмотры %> <комментарии> <репосты> [лайки]\n"
        "например: /tt a-02-11-1 15400 41% 230 96\n"
        "/tt веса — веса отбора и последняя подстройка")


def waiting(con: sqlite3.Connection, root: Path = REPO, days: float = 10) -> list[str]:
    """Delivered viral films of the last days without numbers (a day old at least)."""
    out = []
    for f in sorted((root / "projects").glob("news-viral-*/kinescope.json")):
        age = time.time() - f.stat().st_mtime
        if age > days * 86400:
            continue
        slug = f.parent.name
        if con.execute("SELECT 1 FROM stats WHERE slug=?", (slug,)).fetchone():
            continue
        out.append(short(slug) + ("" if age >= 20 * 3600 else " (ещё нет суток)"))
    return out


def handle(text: str, con: sqlite3.Connection | None = None, root: Path = REPO) -> str:
    """One /tt message from the owner → the bot's answer."""
    con = con or db()
    parts = text.split()
    args = parts[1:] if parts and parts[0].startswith("/") else parts
    if not args:
        wait = waiting(con, root)
        return HELP + ("\n\nЖдут цифр: " + ", ".join(wait) if wait else "\n\nВсе выложенные ролики с цифрами.")
    if args[0].lower() in ("веса", "weights"):
        w = viral.load_weights()
        last = con.execute("SELECT ts, text FROM tune_log ORDER BY id DESC LIMIT 1").fetchone()
        when = datetime.fromtimestamp(last["ts"], MSK).strftime("%d.%m %H:%M") if last else "ещё не было"
        return ("Веса отбора (баллов из 100 на максимуме признака):\n"
                + "\n".join(f"{NAMES.get(k, k)}: {v:.1f}" for k, v in sorted(w.items(), key=lambda kv: -kv[1]))
                + f"\n\nПоследняя подстройка: {when}" + (f"\n{last['text']}" if last else ""))
    slug = resolve(con, args[0], root)
    if not slug:
        return f"Не нашёл ролик «{args[0]}». " + HELP
    try:
        nums = parse(args[1:])
    except ValueError as e:
        return f"⚠️ {e}\n{HELP}"
    add(con, slug, nums, text)
    per = 1000 / nums["views"]
    return (f"✅ {short(slug)}: {nums['views']:,} просмотров, досмотры {nums['completion'] * 100:.0f} %, "
            f"комментариев {nums['comments']} ({nums['comments'] * per:.1f} на 1000), репостов {nums['shares']} "
            f"({nums['shares'] * per:.1f} на 1000). Учту в подстройке весов (по понедельникам).")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tune")
    t.add_argument("--send", action="store_true", help="the report to the owner's bot")
    t.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("register")
    r.add_argument("project", type=Path)
    ad = sub.add_parser("add")
    ad.add_argument("film")
    ad.add_argument("numbers", nargs="+")
    sub.add_parser("list")
    b = sub.add_parser("bot", help="one /tt message → the answer on stdout (factory/service.py runs this)")
    b.add_argument("text")
    a = ap.parse_args()
    con = db()
    if a.cmd == "tune":
        text = tune(con, a.dry_run)
        print(text)
        if a.send:
            import subprocess

            subprocess.run([str(REPO / ".venv" / "bin" / "python"), "addons/mural/send_telegram.py", text], cwd=REPO,
                           check=False)
    elif a.cmd == "register":
        proj = a.project if a.project.is_absolute() else REPO / a.project
        print(register(con, proj))
    elif a.cmd == "add":
        print(handle(" ".join(["/tt", a.film, *a.numbers]), con))
    elif a.cmd == "bot":
        print(handle(a.text, con))
    else:
        for f in latest(con, 365):
            print(f"{short(f['slug'])}: {f['views']:,} просм., досмотры {f['completion'] * 100:.0f} %, "
                  f"комм. {f['comments']}, репостов {f['shares']}, результат {result(f):.2f}")
        print("ждут цифр:", ", ".join(waiting(con)) or "—")


if __name__ == "__main__":
    main()
