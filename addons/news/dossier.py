"""A big story's dossier in one run (README «Большая история»; owner 2026-10-31: «чтобы потом проще было реализовать
такие новости на сервере»): our Telegram channels' posts on it, their media downloaded and described, the country's
TV on YouTube scouted — what the Poland film was made from by hand.

    .venv/bin/python addons/news/dossier.py projects/<slug> "<stem>" ["<stem>" …] [--hours 48] [--fetch 12]
        [--yt "<query in the country's language>" --words "<words>" --lang pl]
    .venv/bin/python addons/news/dossier.py card <screenshot.jpg> <out.jpg>     # a post screenshot as a 1080x760 shot

1. Telegram's own search inside every channel of world.CHANNELS for each stem (the start of a word: «Трибунал»,
   «Навроцк»), merged → projects/<slug>/dossier.json.
2. --fetch N: the media of the N most viewed posts with media — every item of an album — over one Telegram
   connection into media/tg/<channel>_<post>.<jpg|mp4> (the extension from what the file is). Each file is probed:
   size, length, sound; a contact sheet of every video goes to qa/dossier/<file>.jpg — look at them before choosing.
3. --yt: yt_footage.scout over the newest videos (--lang: the country's captions); a staged or generated video is
   flagged and never a shot.
4. projects/<slug>/dossier.md: the timeline (MSK), the media table, the YouTube candidates — the agent writes the
   story from it, checks every figure against the country's own media and the agencies (README), and records
   disagreements in story.json "notes".

card: a screenshot of a post (X, Telegram) is wide and short: under the cover box it would lose its sides. The card
puts it, scaled to 940 px, on a 1080x760 field of its own background colour — a still that keeps all its text, also
under a pan (PAN_Z). "clean": "none" on such a shot only when the agent saw no channel logo on it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import world  # noqa: E402

MSK = timezone(timedelta(hours=3))
CARD_W, CARD_H, CARD_TEXT_W = 1080, 760, 940


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height",
                          "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    d = json.loads(out.stdout or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = any(s.get("codec_type") == "audio" for s in d.get("streams", []))
    still = v.get("codec_name") in ("mjpeg", "png", "webp") and not audio
    dur = float((d.get("format") or {}).get("duration") or 0)
    return {"w": v.get("width"), "h": v.get("height"), "seconds": 0.0 if still else round(dur, 1), "audio": audio,
            "still": still}


async def fetch_all(items: list[tuple[str, int]], dst: Path) -> list[Path]:
    """Every (channel, post) item's photo or video, one Telegram connection for all."""
    import tg_client

    c = tg_client.client()
    await c.connect()
    got = []
    try:
        if not await c.is_user_authorized():
            sys.exit("no Telegram session yet — the owner runs tg_client.py login once")
        for ch, post in items:
            msg = await c.get_messages(ch, ids=post)
            if not msg or not (msg.video or msg.photo or msg.document):
                continue
            ext = "mp4" if msg.video or (msg.document and "video" in (msg.document.mime_type or "")) else "jpg"
            f = dst / f"{ch}_{post}.{ext}"
            if not f.is_file():
                dst.mkdir(parents=True, exist_ok=True)
                await c.download_media(msg, file=str(f))
            if f.is_file() and f.stat().st_size > 1024:
                got.append(f)
    finally:
        await c.disconnect()
    return got


def sheet(video: Path, out: Path, n: int = 6) -> None:
    """n frames across the clip, each with its time, in one strip."""
    secs = probe(video)["seconds"] or 1.0
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf",
                    f"fps={n}/{secs:.3f},scale=-2:240,drawtext=text='%{{pts\\:hms}}':x=6:y=6:fontsize=20:"
                    "fontcolor=yellow:box=1:boxcolor=black@0.6" + f",tile={n}x1", "-frames:v", "1", str(out)],
                   check=False)


def card(src: Path, out: Path) -> Path:
    """A post screenshot on a 1080x760 field of its own background (the colour of its top-left pixel)."""
    from PIL import Image

    im = Image.open(src).convert("RGB")
    bg = im.getpixel((2, 2))
    im = im.resize((CARD_TEXT_W, round(im.height * CARD_TEXT_W / im.width)), Image.LANCZOS)
    if im.height > CARD_H - 40:
        im = im.resize((round(im.width * (CARD_H - 40) / im.height), CARD_H - 40), Image.LANCZOS)
    field = Image.new("RGB", (CARD_W, CARD_H), bg)
    field.paste(im, ((CARD_W - im.width) // 2, (CARD_H - im.height) // 2))
    out.parent.mkdir(parents=True, exist_ok=True)
    field.save(out, quality=94)
    return out


def write_md(proj: Path, stems: list[str], posts: list[dict], media: list[dict], yt: list[dict]) -> Path:
    lines = [f"# Досье: {', '.join(stems)}", "",
             f"{len(posts)} постов, каналы: {', '.join(sorted({p['channel'] for p in posts}))}.", "",
             "## Лента (МСК)", ""]
    for p in posts:
        t = datetime.fromisoformat(p["date"]).astimezone(MSK).strftime("%d.%m %H:%M")
        album = f" ×{len(p['album'])}" if len(p.get("album") or []) > 1 else ""
        lines.append(f"- {t} `{p['ref']}` {p['views']:,} {p['media'] or '—'}{album} — {' '.join(p['text'].split())[:400]}")
    if media:
        lines += ["", "## Медиа (media/tg/)", "", "| файл | кадр | длина | звук | пост |", "|---|---|---|---|---|"]
        for m in media:
            lines.append(f"| {m['file']} | {m['w']}×{m['h']} | {m['seconds'] or 'фото'} | {'да' if m['audio'] else '—'} "
                         f"| {m['ref']} |")
        lines.append("\nЛисты кадров видео: qa/dossier/<файл>.jpg. Логотипы каналов — clean_footage.py --plan.")
    if yt:
        lines += ["", "## YouTube", ""]
        for v in yt:
            m = v["moment"]
            where = (f"{m['from']:.0f}–{m['to']:.0f} с: {m['text'][:140]}" if m else
                     "слова не звучат" if v["captions"] else "нет субтитров")
            flag = " **✗ инсценировка / ИИ — не брать**" if v.get("staged") else ""
            lines.append(f"- `{v['id']}` {v['duration']} с, {v['channel']} — {v['title'][:100]}{flag}\n  {where}")
    out = proj / "dossier.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "card":
        print(card(Path(sys.argv[2]), Path(sys.argv[3])))
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("stems", nargs="+")
    ap.add_argument("--hours", type=float, default=48)
    ap.add_argument("--fetch", type=int, default=12, help="the media of this many most viewed posts")
    ap.add_argument("--yt", help="a YouTube query in the country's language")
    ap.add_argument("--words", default="", help="words the YouTube moment should say")
    ap.add_argument("--lang", default="ru")
    a = ap.parse_args()
    import tg_client

    seen: dict[str, dict] = {}
    for s in a.stems:
        for p in asyncio.run(tg_client.search(s, world.CHANNELS, a.hours)):
            seen[p["ref"]] = p
    posts = sorted(seen.values(), key=lambda p: p["date"])
    a.project.mkdir(parents=True, exist_ok=True)
    (a.project / "dossier.json").write_text(json.dumps(posts, ensure_ascii=False, indent=1), encoding="utf-8")
    media = []
    if a.fetch:
        top = sorted((p for p in posts if p["media"]), key=lambda p: -p["views"])[:a.fetch]
        items = list(dict.fromkeys((p["channel"], i) for p in top for i in (p.get("album") or [p["post"]])))
        refs = {(p["channel"], i): p["ref"] for p in top for i in (p.get("album") or [p["post"]])}
        for f in asyncio.run(fetch_all(items, a.project / "media" / "tg")):
            info = probe(f)
            ch, post = f.stem.rsplit("_", 1)
            media.append({"file": f.name, **info, "ref": refs.get((ch, int(post)), "")})
            if not info["still"]:
                sheet(f, a.project / "qa" / "dossier" / f"{f.stem}.jpg")
    yt = []
    if a.yt:
        import yt_footage

        yt = yt_footage.scout(a.yt, a.words or a.yt, 8, True, a.lang)
    print(write_md(a.project, a.stems, posts, media, yt))
    print(f"{len(posts)} posts, {len(media)} media files, {len(yt)} YouTube candidates")


if __name__ == "__main__":
    main()
