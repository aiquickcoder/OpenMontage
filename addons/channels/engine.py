"""The editor (zasnyali-engine.service): raw posts → stories → the AI's verdict → drafts → written posts.

Every few seconds:
1. cluster — each new post joins the story it shares rare words with (cluster.py) or opens a new one; ads stay out,
   posts the text filters flag (war, propaganda, risky) still join, and their story is dropped as a whole — one
   channel's «после атаки БПЛА» marks the fire it describes as war news.
2. judge — new stories (and stories that spread to more channels) go to the AI in batches (judge.py) with the list
   of what the channels already have: duplicates by meaning are merged, the rest scored and routed to channels.
3. write — each draft becomes the channel's post (rewrite.py), checked for format and facts; then the media worker
   (media_worker.py) finds it a clean picture and the publisher (publisher.py) puts it out on schedule.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from addons.channels import cluster, config, judge, llm, rewrite, tgread

log = logging.getLogger("channels.engine")
REJUDGE_AT = (2, 3, 5, 8)  # a story is judged again when it reaches this many channels
JUDGE_MAX_AGE_H = 8        # a story first seen longer ago is not news any more
MSK = tgread.MSK


class Clusterer:
    def __init__(self, db):
        self.db = db
        self.idx = cluster.Index()
        self.pool: list[tuple[int, dict, float]] = []  # (story_id, sig, ts)
        self.seeds: dict[int, dict] = {}  # story_id -> its first post's signature
        self.inv: dict[str, list[int]] = {}  # stem -> pool indices: only posts sharing a rare stem are compared
        self.built = 0.0

    def rebuild(self) -> None:
        horizon = time.time() - float(config.setting(self.db, "story_window_h")) * 3600
        rows = self.db.q("SELECT story_id, sig, ts FROM raw_posts WHERE ts>=? AND story_id>0 ORDER BY ts", (horizon,))
        self.idx = cluster.Index(r["sig"] for r in rows if isinstance(r["sig"], dict))
        self.pool = [(r["story_id"], r["sig"], r["ts"]) for r in rows if isinstance(r["sig"], dict)]
        self.seeds = {}
        self.inv = {}
        for i, (sid, sig, _) in enumerate(self.pool):
            self.seeds.setdefault(sid, sig)
            for w in sig["s"]:
                self.inv.setdefault(w, []).append(i)
        self.built = time.time()

    def run(self) -> int:
        if time.time() - self.built > 600:
            self.rebuild()
        new = self.db.q("SELECT * FROM raw_posts WHERE story_id IS NULL ORDER BY ts LIMIT 400")
        horizon = time.time() - float(config.setting(self.db, "story_window_h")) * 3600
        for p in new:
            if "реклама" in (p["filtered"] or "") or p["ts"] < horizon or not (p["plain"] or "").strip():
                self.db.update("raw_posts", "id", p["id"], story_id=-1)
                continue
            if cluster.is_digest(p["plain"]):  # a round-up of several stories would glue them together
                self.db.update("raw_posts", "id", p["id"], story_id=-1,
                               filtered=", ".join(x for x in (p["filtered"], "дайджест") if x))
                continue
            sig = p["sig"] if isinstance(p["sig"], dict) else cluster.signature(p["plain"])
            self.idx.add(sig)
            near: set[int] = set()
            for w in sig["s"]:
                if self.idx.rare(w, 0.05):
                    near.update(self.inv.get(w, ()))
            cands = [(self.pool[i][0], self.pool[i][1]) for i in near if abs(self.pool[i][2] - p["ts"]) < 36 * 3600]
            sid = cluster.best_story(sig, cands, self.idx, self.seeds)
            while sid is not None:  # a story merged by the judge lives on in the one it joined
                row = self.db.one("SELECT status, merged_into FROM stories WHERE id=?", (sid,))
                if row and row["status"] == "merged" and row["merged_into"]:
                    sid = row["merged_into"]
                    continue
                break
            with self.db.tx():
                if sid is None or not self.db.one("SELECT id FROM stories WHERE id=? AND status!='merged'", (sid,)):
                    sid = self.db.insert("stories", {"created": time.time(), "updated": time.time(), "first_ts": p["ts"],
                                                     "lead": p["plain"][:300], "status": "new"})
                self.db.update("raw_posts", "id", p["id"], story_id=sid)
                self.refresh_story(sid)
            self.pool.append((sid, sig, p["ts"]))
            for w in sig["s"]:
                self.inv.setdefault(w, []).append(len(self.pool) - 1)
            self.seeds.setdefault(sid, sig)
        return len(new)

    def refresh_story(self, sid: int) -> None:
        posts = self.db.q("SELECT channel, views, kind, media, filtered, ts FROM raw_posts WHERE story_id=?", (sid,))
        st = self.db.one("SELECT * FROM stories WHERE id=?", (sid,))
        chans = {p["channel"] for p in posts}
        fields = {"n_posts": len(posts), "n_channels": len(chans), "max_views": max((p["views"] for p in posts), default=0),
                  "has_primary": int(any(p["kind"] == "primary" for p in posts)),
                  "has_media": int(any(p["media"] for p in posts)), "updated": time.time(),
                  "first_ts": min(p["ts"] for p in posts)}
        flags = sorted({f.strip() for p in posts for f in (p["filtered"] or "").split(",") if f.strip()})
        if flags and st["status"] not in ("rejected",):
            fields.update(status="rejected", reason="фильтр: " + ", ".join(flags))
        elif st["status"] in ("judged", "selected") and len(chans) in REJUDGE_AT and len(chans) > (st["n_channels"] or 0):
            fields["status"] = "grown"
        self.db.update("stories", "id", sid, **fields)


def story_posts(db, sid: int) -> list[dict]:
    rows = db.q("SELECT * FROM raw_posts WHERE story_id=? ORDER BY ts", (sid,))
    for r in rows:
        r["date_msk"] = datetime.fromtimestamp(r["ts"], MSK).strftime("%d.%m %H:%M")
    return rows


def recent_titles(db, hours: float = 24) -> list[dict]:
    rows = db.q("SELECT id, story_id, title, channel FROM posts WHERE created>=? AND title!='' AND "
                "status NOT IN ('rejected') ORDER BY id DESC LIMIT 120", (time.time() - hours * 3600,))
    seen, out = set(), []
    for r in rows:
        if r["story_id"] in seen:
            continue
        seen.add(r["story_id"])
        out.append({"id": r["story_id"], "title": r["title"]})
    return out


def merge(db, into: int, sid: int) -> None:
    with db.tx():
        db.x("UPDATE raw_posts SET story_id=? WHERE story_id=?", (into, sid))
        db.update("stories", "id", sid, status="merged", merged_into=into)
    Clusterer(db).refresh_story(into)


def judge_pending(db, batch: int = 10) -> int:
    horizon = time.time() - JUDGE_MAX_AGE_H * 3600
    rows = db.q("SELECT * FROM stories WHERE status IN ('new','grown') ORDER BY first_ts DESC LIMIT 30")
    todo = []
    for s in rows:
        if s["first_ts"] < horizon:
            db.update("stories", "id", s["id"], status="old")
            continue
        todo.append(s)
    if not todo:
        return 0
    chans = db.channels()
    trust = {r["ref"]: r["trust"] for r in db.q("SELECT ref, trust FROM sources")}
    trust.update({r["title"]: r["trust"] for r in db.q("SELECT title, trust FROM sources WHERE platform!='telegram'")})
    done = 0
    batches = [todo[i:i + batch] for i in range(0, len(todo), batch)]

    def ask(part: list[dict]) -> tuple[list, dict | Exception]:
        own = config.db()  # a thread's own connection: sqlite3 connections are not shared between threads
        loaded = [(s, story_posts(own, s["id"])) for s in part]
        loaded = [(s, p) for s, p in loaded if p]
        try:
            return loaded, {int(v["id"]): v for v in judge.ask(own, loaded, recent_titles(own))
                            if str(v.get("id", "")).isdigit()}
        except (llm.BudgetExceeded, llm.LLMError) as e:
            return loaded, e

    with ThreadPoolExecutor(3) as ex:
        results = list(ex.map(ask, batches))
    for loaded, verdicts in results:
        if isinstance(verdicts, llm.BudgetExceeded):
            db.event("budget", str(verdicts))
            return done
        if isinstance(verdicts, Exception):
            db.event("judge", f"ошибка ИИ: {verdicts}"[:300])
            continue
        for s, posts in loaded:
            v = verdicts.get(s["id"])
            now = db.one("SELECT status FROM stories WHERE id=?", (s["id"],))
            if not v or not now or now["status"] == "merged":
                continue
            done += 1
            same = v.get("same_as")
            if same not in (None, "", "null"):
                ref = str(same).lstrip("Pp")
                if ref.isdigit() and int(ref) != s["id"]:
                    target = int(ref)
                    if db.one("SELECT id FROM stories WHERE id=? AND status!='merged'", (target,)):
                        merge(db, min(target, s["id"]), max(target, s["id"]))
                        if target < s["id"]:
                            # the older story (already judged or published) keeps the lead; the new posts join it
                            st = db.one("SELECT status FROM stories WHERE id=?", (target,))
                            if st and st["status"] in ("judged", "selected"):
                                db.update("stories", "id", target, status="grown")
                        continue
            sig = judge.signals(posts, trust)
            sc = judge.score(v, sig, float(config.setting(db, "primary_bonus")))
            flags = [k for k in ("war", "ad", "propaganda", "politics", "risky") if v.get(k)]
            fields = {"judge": v, "score": sc, "region": v.get("region") or "", "category": v.get("category") or "",
                      "title": (v.get("title") or "")[:200], "judged_at": time.time(), "judged_posts": len(posts)}
            if flags:
                fields.update(status="rejected", reason="ИИ: " + ", ".join(flags))
            else:
                targets = judge.channels_for(v, sc, chans)
                fields.update(status="selected" if targets else "judged",
                              reason=(v.get("why") or "")[:300])
                urgent = bool(v.get("breaking")) and sc >= 8
                for key in targets:
                    db.insert("posts", {"story_id": s["id"], "channel": key, "created": time.time(), "status": "draft",
                                        "score": sc, "urgent": int(urgent), "title": fields["title"],
                                        "updated": time.time()}, ignore=True)
            db.update("stories", "id", s["id"], **fields)
    return done


def write_drafts(db, limit: int = 4) -> int:
    rows = db.q("SELECT * FROM posts WHERE status='draft' ORDER BY urgent DESC, score DESC, id LIMIT ?", (limit,))
    n = 0
    for p in rows:
        ch = db.channel(p["channel"])
        st = db.one("SELECT * FROM stories WHERE id=?", (p["story_id"],))
        if not ch or not st:
            continue
        if time.time() - st["first_ts"] > float(ch.get("max_age_h") or 8) * 3600:
            db.update("posts", "id", p["id"], status="expired", error="устарело до написания", updated=time.time())
            continue
        posts = story_posts(db, p["story_id"])
        try:
            res = rewrite.write(db, ch, posts, st["title"])
        except llm.BudgetExceeded as e:
            db.event("budget", str(e))
            return n
        except llm.LLMError as e:
            db.update("posts", "id", p["id"], error=f"ИИ: {e}"[:300], updated=time.time())
            continue
        n += 1
        links = [x["link"] for x in sorted(posts, key=lambda x: (x["kind"] != "primary", -x["views"]))][:8]
        if res.get("ok"):
            db.update("posts", "id", p["id"], status="media", html=res["html"], sources=links,
                      facts=(res.get("data") or {}).get("facts") or [], model=config.setting(db, "model_write"),
                      updated=time.time(), error="")
        else:
            db.update("posts", "id", p["id"], status="rejected", html=res.get("html") or "", sources=links,
                      error=(res.get("why") or "не прошло проверку")[:500], updated=time.time())
    return n


def loop() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = config.db()
    from addons.channels import seed

    seed.seed(db)
    cl = Clusterer(db)
    db.event("engine", "редактор запущен")
    while True:
        try:
            t0 = time.time()
            a = cl.run()
            t1 = time.time()
            b = judge_pending(db)
            t2 = time.time()
            c = write_drafts(db)
            if a or b or c:
                log.info("tick: clustered %d (%.1fs), judged %d (%.1fs), written %d (%.1fs)", a, t1 - t0, b, t2 - t1, c,
                         time.time() - t2)
        except Exception as e:  # noqa: BLE001 — one bad tick must not stop the editor
            log.exception("tick failed")
            db.event("engine", f"сбой: {type(e).__name__}: {e}"[:300])
            time.sleep(20)
        db.set_setting("engine_alive", time.time())
        time.sleep(5)


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


if __name__ == "__main__":
    loop()
