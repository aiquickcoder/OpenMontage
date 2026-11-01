"""The media worker (zasnyali-media.service): a written post gets its clean pictures, then it is ready to publish.

Candidates: the story's posts with media, the first-hand source first (official footage), then by reach × trust; up
to four posts are tried, an album gives up to max_media files. Each file goes through media.clean_cached (look →
crop or clean → look again); what fails is noted and the next file / post is tried; with nothing clean the post goes
without a picture (the owner's rule).

Urgent posts (the judge's «breaking»): only the fast ways (no cleaner) before publishing — a photo, or a clean still
frame of the video; the video itself is cleaned after, and editMessageMedia swaps it into the same post
(owner/coordinator 2026-11-01). One file at a time; video cleaning waits while a news film is being made.
"""
from __future__ import annotations

import hashlib
import logging
import time
import urllib.request
from pathlib import Path

from addons.channels import config, engine, media, publish

log = logging.getLogger("channels.media")
DOWNLOAD_WAIT_S = 240
KEEP_DAYS = 3


def candidates(db, story_id: int) -> list[dict]:
    posts = [p for p in engine.story_posts(db, story_id) if p.get("media")]
    return sorted(posts, key=lambda p: (p["kind"] != "primary", -(p["views"] or 0) * float(p["trust"] or 0.6)))


def wanted(db, items: list[dict]) -> list[dict]:
    vmax = float(config.setting(db, "video_max_s"))
    out = []
    for m in items:
        if m.get("kind") == "video" and (m.get("duration") or 0) > vmax:
            continue
        if (m.get("size") or 0) > 400 * 2**20:
            continue
        out.append(m)
    return out[:int(config.setting(db, "max_media"))]


def fetch_tg(db, channel: str, post_ids: list[int]) -> list[Path]:
    did = db.insert("downloads", {"created": time.time(), "channel": channel, "posts": post_ids})
    t0 = time.time()
    while time.time() - t0 < DOWNLOAD_WAIT_S:
        d = db.one("SELECT * FROM downloads WHERE id=?", (did,))
        if d["status"] == "done":
            return [Path(p) for p in d["paths"] or []]
        if d["status"] == "failed":
            raise media.Reject(f"не скачалось: {d['error']}")
        time.sleep(3)
    db.update("downloads", "id", did, status="failed", error="timeout")
    raise media.Reject("сборщик не успел скачать медиа")


def fetch_url(url: str, dst_dir: Path) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    ext = ".jpg" if not url.lower().split("?")[0].endswith((".png", ".webp")) else Path(url.split("?")[0]).suffix
    dst = dst_dir / (hashlib.sha1(url.encode()).hexdigest()[:16] + ext)
    if not dst.is_file():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            dst.write_bytes(r.read()[:30 * 2**20])
    return dst


def files_of(db, rp: dict) -> list[tuple[Path, str]]:
    items = wanted(db, rp["media"] or [])
    out: list[tuple[Path, str]] = []
    tg = [m for m in items if m.get("post")]
    if tg:
        paths = fetch_tg(db, rp["channel"], [m["post"] for m in tg])
        by_id = {}
        for p in paths:
            try:
                by_id[int(p.stem.rsplit("_", 1)[-1])] = p
            except ValueError:
                pass
        for m in tg:
            if m["post"] in by_id:
                out.append((by_id[m["post"]], m["kind"]))
    for m in items:
        if m.get("url"):
            try:
                out.append((fetch_url(m["url"], config.MEDIA / "src"), "photo"))
            except Exception as e:  # noqa: BLE001
                log.info("photo %s: %s", m["url"], e)
        elif m.get("ref"):
            from addons.news import sources as ns

            dst = config.MEDIA / "src" / (hashlib.sha1(m["ref"].encode()).hexdigest()[:16] + ".mp4")
            if dst.is_file() or ns.fetch_video(m["ref"], dst):
                out.append((dst, "video"))
    return out


def prepare(db, post: dict) -> tuple[list[dict], list[str], dict]:
    """(media, notes, upgrade) for a post."""
    urgent = bool(post.get("urgent"))
    work = config.MEDIA / "posts" / str(post["id"])
    work.mkdir(parents=True, exist_ok=True)
    got: list[dict] = []
    notes: list[str] = []
    upgrade: dict = {}
    for rp in candidates(db, post["story_id"])[:4]:
        try:
            files = files_of(db, rp)
        except media.Reject as e:
            notes.append(f"{rp['channel']}: {e}")
            continue
        for i, (f, kind) in enumerate(files):
            dst = work / f"{rp['channel']}_{rp['post'] or i}_{i}"
            try:
                res = media.clean_cached(db, f, dst, urgent, allow_slow=not urgent)
                got.append(res)
            except media.Slow:
                still = media.still_of(media.normalize(f, dst.with_name(dst.name + ".norm")), dst.with_name(dst.name + "_still"))
                try:
                    got.append(media.clean_cached(db, still, dst.with_name(dst.name + "_stillc")))
                    upgrade = {"src": str(f), "dst": str(dst.with_name(dst.name + "_up")), "state": "pending",
                               "index": len(got) - 1}
                    notes.append(f"{f.name}: пока стоп-кадр, видео дочищается")
                except media.Reject as e:
                    notes.append(f"{f.name}: стоп-кадр: {e}")
            except media.Reject as e:
                notes.append(f"{f.name}: {e}")
            except Exception as e:  # noqa: BLE001 — a broken file is skipped, never published
                notes.append(f"{f.name}: сбой {type(e).__name__}: {e}"[:200])
            if len(got) >= int(config.setting(db, "max_media")):
                break
        if got:
            break
    return got, notes, upgrade


def run_upgrade(db, post: dict) -> None:
    up = post["upgrade"]
    ch = db.channel(post["channel"])
    try:
        res = media.clean_cached(db, Path(up["src"]), Path(up["dst"]), urgent=False, allow_slow=True)
        if res["kind"] != "video":
            raise media.Reject("не видео")
        live = post["status"] == "published"
        tg = publish.channel_bot(ch) if live else publish.owner()
        idx = int(up.get("index") or 0)
        ids = post.get("message_ids") or []
        if idx >= len(ids):
            raise media.Reject("нет сообщения для замены")
        caption = post["html"] if idx == 0 else ""
        publish.edit_media(tg, post["chat_id"], ids[idx], Path(res["path"]), "video", caption)
        m = list(post.get("media") or [])
        if idx < len(m):
            m[idx] = res
        db.update("posts", "id", post["id"], media=m, upgrade={**up, "state": "done"}, updated=time.time(),
                  media_note=(post.get("media_note") or "") + " · видео дочищено и заменено")
        db.event("media", f"пост {post['id']}: стоп-кадр заменён очищенным видео")
    except media.Reject as e:
        db.update("posts", "id", post["id"], upgrade={**up, "state": "failed", "why": str(e)[:200]}, updated=time.time())
    except Exception as e:  # noqa: BLE001
        db.update("posts", "id", post["id"], upgrade={**up, "state": "failed", "why": f"{type(e).__name__}: {e}"[:200]})


def tidy() -> None:
    horizon = time.time() - KEEP_DAYS * 86400
    for p in config.MEDIA.rglob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < horizon:
                p.unlink()
        except OSError:
            pass


READY_AHEAD = 4  # a channel with this many ready posts gets pictures only for a post that would beat them


def next_post(db) -> dict | None:
    """The next post to find pictures for: urgent first, then by score — but a channel whose ready queue is already
    full only gets a post that scores above the weakest one there (the rest would expire unpublished)."""
    ready = {r["channel"]: (r["n"], r["low"]) for r in db.q(
        "SELECT channel, COUNT(*) n, MIN(score) low FROM posts WHERE status='ready' GROUP BY channel")}
    for p in db.q("SELECT * FROM posts WHERE status='media' ORDER BY urgent DESC, score DESC, id LIMIT 50"):
        n, low = ready.get(p["channel"], (0, 0))
        if p["urgent"] or n < READY_AHEAD or p["score"] > (low or 0):
            return p
    return None


def tick(db) -> bool:
    post = next_post(db)
    if post:
        got, notes, upgrade = prepare(db, post)
        note = "; ".join([f"{m['kind']}: {m['how']}" for m in got] + (["отбраковано: " + "; ".join(notes)] if notes else []))
        db.update("posts", "id", post["id"], status="ready", media=got, upgrade=upgrade or "",
                  media_note=(note or "у сюжета нет медиа")[:1500], updated=time.time())
        return True
    for p in db.q("SELECT * FROM posts WHERE status IN ('published','previewed') AND upgrade LIKE '%pending%' "
                  "ORDER BY id DESC LIMIT 1"):
        run_upgrade(db, p)
        return True
    return False


def loop() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = config.db()
    if not media.env_ok():
        db.event("media", "нет APP_USERNAME/APP_PASSWORD сервиса очистки — только обрезка и проверка")
    db.event("media", "медиа-обработчик запущен")
    last_tidy = 0.0
    while True:
        try:
            busy = tick(db)
        except Exception as e:  # noqa: BLE001
            log.exception("tick failed")
            db.event("media", f"сбой: {e}"[:300])
            busy = False
        if time.time() - last_tidy > 6 * 3600:
            tidy()
            last_tidy = time.time()
        db.set_setting("media_alive", time.time())
        time.sleep(2 if busy else 10)


if __name__ == "__main__":
    loop()
