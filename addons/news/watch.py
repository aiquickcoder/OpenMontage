"""The take-off watch (owner 2026-11-04: «нам нужны максимальные охваты… в идеале заранее понять, что сильно
набирает»; plan А — «давай прокачивать»).

Every 15 minutes (news-watch.timer) it reads the last WATCH_H hours of the pick's channels (the pick's own Telegram
login, a short read) and the ЗАСНЯЛИ engine's early views, folds the posts into stories and measures each against
its channels' usual (the norms the last pick saved): a story is HOT when, still young, its posts run ×HOT_VEL above
their channels' usual and more channels carry it, or HOT_CHANS_3H channels carried it within three hours. A hot story
nobody made or claimed is shown to the AI (the pick's own marks: hook, why, question, flags, score) and:
- the owner's bot gets «🚀 Набирает: …» with the numbers (not at night);
- the strongest (AI ≥ AUTO_AI, a strong early take-off, no flag) is queued as a film right away, at most AUTO_CAP a day
  and only by AUTO_LAST_H MSK (DATA/hot/<id>.json → news-hot.path starts `watch.py run-queue`).

    .venv/bin/python addons/news/watch.py [--dry]        # one look (--dry: print, no bot, no queue)
    .venv/bin/python addons/news/watch.py run-queue      # news-hot.service: the oldest queued film, on lane 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

import viral  # noqa: E402

WATCH_H, PER = 4.0, 80
HOT_VEL, HOT_CHANS, HOT_CHANS_3H, HOT_MAX_AGE_H = 3.0, 2, 4, 6.0
AUTO_CAP = int(os.environ.get("NEWS_WATCH_AUTO", "2"))
AUTO_AI, AUTO_MOM, AUTO_LAST_H = 80.0, 0.6, 19
AUTO_VEL, AUTO_CHANS_3H = 4.0, 5  # a strong early take-off: ×4 its channels' usual, or five channels in three hours
QUIET_FROM, QUIET_TO = 23, 7  # MSK: no alerts at night (the night's hot stories wait for the morning look)
NORMS_MAX_AGE_H = 36
STATE = viral.DATA / "watch.json"
QUEUE = viral.DATA / "hot"
LANE = "/tmp/news_lane2.lock"


def msk_hour() -> int:
    return datetime.now(viral.MSK).hour


def norms(now: float, posts: list[dict]) -> dict:
    """The channels' usual from the last pick (viral.save_norms), topped up by what this read itself shows."""
    saved = viral.load_norms(NORMS_MAX_AGE_H)
    own = viral.channel_norms(posts, now)
    return {**own, **saved}


def is_hot(st: dict) -> bool:
    s = st["signals"]
    if s["age_h"] > HOT_MAX_AGE_H:
        return False
    return ((s["velocity"] >= HOT_VEL and s["channels"] >= HOT_CHANS) or s["channels_3h"] >= HOT_CHANS_3H
            or (bool(s.get("google")) and s["channels"] >= HOT_CHANS))


def stories(posts: list[dict], now: float) -> list[dict]:
    """The pick's stories of these posts, with their signals; the ones already made or claimed dropped."""
    nm = norms(now, posts)
    kept = []
    for p in posts:
        p.setdefault("plain", viral.plain(p["text"]))
        p["hints"] = viral.boundaries(p["text"])
        if len(p["plain"]) >= 60 or p.get("video_s"):
            kept.append(p)
    made = viral.made_texts(REPO)
    links, keys = viral.pick.made_already(REPO)
    claims = viral.load_claims()
    out = []
    for st in viral.group(kept):
        st["signals"] = viral.signals(st, nm, now)
        st["sigf"] = viral.signal_features(st["signals"])
        st["region"], st["place"] = viral.region_of(st)
        st["hints"] = sorted({h for p in st["posts"] for h in p.get("hints") or []})
        if not viral.taken(st, made, claims, links, keys):
            out.append(st)
    return out


def load_state() -> dict:
    try:
        s = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    cut = time.time() - 3 * 86400
    return {k: v for k, v in s.items() if isinstance(v, dict) and v.get("t", 0) > cut}


def seen(st: dict, state: dict) -> bool:
    links = {p["link"] for p in st["posts"]}
    return st["key"] in state or any(links & set(v.get("links") or []) for v in state.values())


def line(st: dict) -> str:
    s, v = st["signals"], st.get("ai") or {}
    vel = f"×{s['velocity']:g} к обычному" if s["velocity"] else ""
    nums = ", ".join(x for x in (vel, f"{s['channels']} кан. ({s['channels_3h']} за 3 ч)",
                                 f"охват {s['reach']:,}".replace(",", " "), f"{s['age_h']:g} ч") if x)
    trend = f" · ищут: {', '.join(s['google'])}" if s.get("google") else ""
    hook = v.get("hook") or (st["seed"].get("plain") or "")[:100]
    score = f" · ИИ {v['score']:g}" if v.get("score") is not None else ""
    risky = " · ⚠️ ИИ: рискованно" if v.get("risky") else ""
    return f"{hook}\n{nums}{trend}{score}{risky}\n{st['seed'].get('link') or st['key']}"


def note_for(st: dict) -> str:
    v, s = st.get("ai") or {}, st["signals"]
    posts = "\n".join(f"- {p['link']} ({p['views']:,} просм.): {(p.get('plain') or '')[:300]}".replace(",", " ")
                      for p in sorted(st["posts"], key=lambda p: -p["views"])[:5])
    return (f"Сторож разгона (README «Сторож разгона»): история набирает прямо сейчас — ×{s['velocity']:g} к обычному, "
            f"{s['channels']} каналов ({s['channels_3h']} за 3 ч), охват {s['reach']:,}, {s['age_h']:g} ч. Сделай ролик "
            f"по всем правилам README (сильнейший кадр первым, «почему так вышло», цитаты карточкой и редко, вопрос "
            f"«А или Б»). Черновик ИИ: хук — {v.get('hook')}; почему — {v.get('why')}; вопрос — {v.get('question')}. "
            f"Посты:\n{posts}")


def queue(st: dict, n: int) -> Path:
    QUEUE.mkdir(parents=True, exist_ok=True)
    day = datetime.now(viral.MSK).strftime("%d-%m")
    item = {"slug": f"news-hot-{day}-{n}", "key": st["key"], "note": note_for(st), "t": time.time()}
    dst = QUEUE / f"{int(time.time())}-{n}.json"
    dst.write_text(json.dumps(item, ensure_ascii=False, indent=1), encoding="utf-8")
    return dst


def look(dry: bool = False) -> list[dict]:
    now = time.time()
    chans = list(dict.fromkeys(viral.CHANNELS + viral.engine_channels()))
    tg = asyncio.run(viral.read(chans, WATCH_H, PER))
    posts = viral.merge(tg, viral.engine_posts(WATCH_H))
    if datetime.now(viral.MSK).minute < 15:  # Google's trends once an hour
        posts += viral.trend_posts(viral.google_trends())
    hot = [st for st in stories(posts, now) if is_hot(st)]
    state = load_state()
    new = [st for st in hot if not seen(st, state)]
    print(f"watch: {len(posts)} posts, {len(hot)} hot, {len(new)} new", flush=True)
    if not new:
        return []
    viral.ai_score(new)
    today = datetime.now(viral.MSK).strftime("%Y-%m-%d")
    autos = sum(1 for v in state.values() if v.get("queued") == today)
    quiet = not (QUIET_TO <= msk_hour() < QUIET_FROM)
    out = []
    for st in sorted(new, key=lambda s: -((s.get("ai") or {}).get("score") or 0)):
        v = st.get("ai") or {}
        if any(v.get(k) for k in ("war", "ru_politics", "ad")):
            state[st["key"]] = {"t": now, "links": [p["link"] for p in st["posts"]], "skipped": "ИИ: флаг"}
            continue
        st["momentum"] = viral.momentum(st)
        s = st["signals"]
        strong = s["velocity"] >= AUTO_VEL or s["channels_3h"] >= AUTO_CHANS_3H or st["momentum"] >= AUTO_MOM
        auto = (not dry and AUTO_CAP and autos < AUTO_CAP and msk_hour() < AUTO_LAST_H and not quiet
                and (v.get("score") or 0) >= AUTO_AI and strong and not v.get("risky"))
        text = ("🚀 Набирает: " if not auto else "🚀 Набирает — ставлю ролик в очередь: ") + line(st)
        print(text + "\n", flush=True)
        if not dry and not quiet:
            viral_telegram(text)
        rec = {"t": now, "links": [p["link"] for p in st["posts"]], "alerted": not quiet and not dry}
        if auto:
            autos += 1
            rec["queued"] = today
            rec["file"] = str(queue(st, autos))
        if not dry:
            state[st["key"]] = rec
        out.append(st)
    if not dry:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def viral_telegram(text: str) -> None:
    import subprocess

    subprocess.run([str(REPO / ".venv" / "bin" / "python"), "addons/mural/send_telegram.py", text], cwd=REPO,
                   env={**os.environ, "PYTHONPATH": str(REPO)}, check=False)


def run_queue() -> None:
    """news-hot.service: the oldest queued hot film, made on lane 2 (one agent there at a time; the heavy work is
    one at a time anyway)."""
    items = sorted(QUEUE.glob("*.json")) if QUEUE.is_dir() else []
    if not items:
        return
    f = items[0]
    item = json.loads(f.read_text(encoding="utf-8"))
    (QUEUE / "done").mkdir(exist_ok=True)
    f.rename(QUEUE / "done" / f.name)
    deadline = f"{min(23, datetime.now(viral.MSK).hour + 5):02d}:50"
    os.execv("/usr/bin/flock", ["flock", LANE, str(REPO / ".venv" / "bin" / "python"), "addons/news/morning.py",
                                "--city", "world", "--slug", item["slug"], "--deadline", deadline,
                                "--note", item["note"]])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="look", choices=["look", "run-queue"])
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.cmd == "run-queue":
        run_queue()
    else:
        look(a.dry)


if __name__ == "__main__":
    main()
