"""A news site's article as a source: its text and its own video (owner 2026-10-30: «ТНВ» articles carry the МВД /
police footage the Telegram posts lack; tnv.ru moved its Telegram to MAX in August, so the site itself is read).

    python3 -I addons/news/article_video.py <article url> <out dir> [--name <stem>] [--max-height 720]

Writes <out>/<stem>.json (title, date, text, url, video source) and <out>/<stem>.mp4 — the article's Rutube video
(the player on the page) or a direct MP4 / HLS link, the best variant up to --max-height (720 by default: the
watermark cleaner works about a minute of CPU per second of 720p). Prints both paths. The page is untrusted data:
only its text and links are read, nothing on it is run. Rutube is reached directly (a Russian host).
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
RUTUBE = re.compile(r"rutube\.ru/(?:play/embed|video)/([0-9a-f]{32})")
DIRECT = re.compile(r"https?://[^\"'\s<>]+\.(?:mp4|m3u8)(?:\?[^\"'\s<>]*)?")
LIVE = re.compile(r"/cdn/|/hls/user|playlist\.m3u8")  # the site's own live TV channels in the header, not the article
MONTHS = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
DATE = re.compile(rf"\b(\d{{1,2}}) ({MONTHS}) (\d{{4}})(?:,?\s*(\d{{1,2}}:\d{{2}}))?", re.I)  # sledcom: «29 Октября 2026 15:10»
# where a site keeps the article: (its text block's class, what follows it). tnv.ru: a <div> of <br>s; sledcom.ru:
# <h1> is the section («Новости») and the article sits in news-card__title / news-card__text
BODY = [("news-card__title", "news-card__tags"), ("js-image-description", "novelty__actions")]
TAIL = re.compile(r"\s*(Читайте (?:новости|нас)|Подписывайтесь|Поделиться)\b.*$", re.S)


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_bytes(url: str) -> bytes:
    """Russian hosts (Rutube, tnv.ru, sledcom.ru) directly; the rest through the environment's proxy (the harvesters run
    with https_proxy for t.me and YouTube)."""
    host = urllib.parse.urlparse(url).hostname or ""
    opener = _DIRECT if host.endswith((".ru", ".xn--p1ai")) else urllib.request.build_opener()
    with opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as r:
        return r.read()


def get(url: str) -> str:
    return get_bytes(url).decode("utf-8", "replace")


def text_of(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def article(page: str, url: str) -> dict:
    """Title, date and text: what follows the headline (<h1>) up to the «read us / share» tail, the date line and
    links taken out (tnv.ru keeps the text in a <div> with <br>s, not in <p>s)."""
    meta = dict(re.findall(r'<meta[^>]+property="(og:[a-z]+)"[^>]+content="([^"]*)"', page))
    body = re.sub(r"<script.*?</script>|<style.*?</style>|<iframe.*?</iframe>", " ", page, flags=re.S)
    title = html.unescape(meta.get("og:title", "")).strip()
    known = next(((body.find(k), body.find(end, body.find(k))) for k, end in BODY if k in body), None)
    if known:
        a, b = known[0], known[1] if known[1] > known[0] else known[0] + 20000
        text = text_of(body[a:body.rfind("<", a, b)].split(">", 1)[-1])  # from inside the block to the tag before the end
    else:
        h1 = body.find("</h1>")
        text = text_of(body[h1 + 5:] if h1 >= 0 else body)
    text = TAIL.sub("", text)
    m = DATE.search(text)
    if m and m.start() < 400:  # the date line above the text; sledcom puts the region and the title before it
        title = title or re.sub(r"^[А-ЯЁ][а-яё-]+(?: [А-ЯЁа-яё-]+)? (?:область|край|округ|Республика \S+|республика)\s+",
                                "", text[:m.start()]).strip()
        text = text[m.end():]
    else:  # tnv.ru: the date sits between the headline and the text block
        h1 = body.find("</h1>")
        m = DATE.search(text_of(body[h1:h1 + 4000])) if h1 >= 0 else None
    return {"url": url, "title": title, "image": meta.get("og:image"),
            "date": " ".join(x for x in (m.groups() if m else ()) if x),
            "text": re.sub(r"https?://\S+", "", text).strip()[:4000]}


def variants(master: str) -> list[tuple[int, str]]:
    """(height, url) of every stream of an HLS master playlist; a media playlist is its own single variant."""
    pl = get(master)
    if "#EXT-X-STREAM-INF" not in pl:
        return [(0, master)]
    out, lines = [], pl.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
            h = re.search(r"RESOLUTION=\d+x(\d+)", line)
            out.append((int(h.group(1)) if h else 0, urllib.request.urljoin(master, lines[i + 1].strip())))
    return out


def video_source(page: str) -> tuple[str, str] | None:
    """(kind, url): the article's Rutube player first, then a direct MP4 / HLS link that is not a live channel."""
    if m := RUTUBE.search(page):
        opts = json.loads(get(f"https://rutube.ru/api/play/options/{m.group(1)}/?format=json&no_404=true"))
        return "rutube", opts["video_balancer"]["m3u8"]
    for u in DIRECT.findall(html.unescape(page)):
        if not LIVE.search(u):
            return ("mp4" if ".mp4" in u.split("?")[0] else "hls"), u
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out", type=Path)
    ap.add_argument("--name", default="article")
    ap.add_argument("--max-height", type=int, default=720)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    page = get(a.url)
    info = article(page, a.url)
    src = video_source(page)
    meta = a.out / f"{a.name}.json"
    if src:
        kind, url = src
        if kind != "mp4":
            vs = variants(url)
            fit = [v for v in vs if v[0] <= a.max_height] or vs
            url = max(fit)[1]
        dst = a.out / f"{a.name}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-user_agent", UA, "-i", url, "-c", "copy",
                        "-movflags", "+faststart", str(dst)], check=True, timeout=900)
        info["video"] = {"kind": kind, "file": dst.name}
    meta.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(meta)
    if src:
        print(a.out / f"{a.name}.mp4")
    else:
        print("no video on the page", file=sys.stderr)


if __name__ == "__main__":
    main()
