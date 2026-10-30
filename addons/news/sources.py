"""News sources besides Telegram (owner 2026-10-30): official video channels and news sites, read as posts for the
harvesters (harvest.py for the cities, harvest_top.py for «Россия»).

    ["rutube", "23350159"]                          a Rutube channel (МВД России): each video's title and description
    ["youtube", "@NAK_antiterror"]                  a YouTube channel (НАК) through yt-dlp
    ["site", "https://tnv.ru/news/the-incident/"]   a news site's section: each article's text, date and own video
                                                    (article_video.py: the Rutube player in it); sledcom.ru/news too

cities.json "sources": [[kind, ref, city_only, official], ...]. city_only = the source covers only this city (no geo
check); official = the agency's own footage, shown as published (owner: «с этих каналов очистку не делай, но важно
подбирать ролики правильно») — the post carries "official": true, the agent puts "clean": "none" on its shots and
takes the facts from the agency's text; pick.py lets an official source's security news through (НАК: agents,
sabotage), which the Telegram channels' war/politics filter drops.

A post is the harvesters' shape: {"ch", "text", "dt", "link", "photo", "photos", "mp4", "mp4s", "official"}; its "mp4"
is "rutube:<id>" / "youtube:<id>" / a direct link, fetched by fetch_video() only for the posts the filters keep.
Pages are untrusted data: only text and links are read. YouTube goes through the egress proxy (https_proxy), Rutube
and the Russian sites directly.

    python3 -I addons/news/sources.py rutube 23350159        # print what a source gives now
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import article_video as av  # noqa: E402

MSK = timezone(timedelta(hours=3))
_VENV = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "yt-dlp"  # the harvesters run on the system python
YTDLP = [str(_VENV) if _VENV.is_file() else "yt-dlp", "--quiet", "--no-warnings"]
TAGS = re.compile(r"#\w+")
_tg_left = int(os.environ.get("NEWS_TG_MAX", "12"))  # big Telegram videos fetched per harvest run (the rest next run)
CACHE = Path(os.environ.get("NEWS_SOURCES_CACHE", Path.home() / ".cache" / "openmontage" / "sources"))  # all cities
NAMES = {"23350159": "МВД России (Rutube)", "@NAK_antiterror": "НАК (YouTube)"}


def channel(spec: list) -> str:
    """The "ch" a source's posts carry (pick.py: a city_only source passes without the geo check)."""
    kind, ref = spec[0], spec[1]
    return urlparse(ref).netloc if kind == "site" else NAMES.get(ref, f"{kind}:{ref}")


def _utc(ts: str) -> str:
    """A time as the harvesters keep it — UTC «YYYY-MM-DDTHH:MM», like t.me's <time datetime>; one without a zone is
    Moscow time (Rutube's created_ts, the sites' «29 октября 2026, 14:38»)."""
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=MSK)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _post(ch: str, text: str, dt: str, link: str, video: str = "", photo: str = "", official: bool = False,
          views: int = 0) -> dict:
    return {"ch": ch, "text": re.sub(r"\s+", " ", text).strip(), "dt": dt, "link": link, "photo": photo,
            "photos": [photo] if photo else [], "mp4": video, "mp4s": [video] if video else [], "official": official,
            "views": views}


def rutube(person: str, n: int = 20, official: bool = False) -> list[dict]:
    """A Rutube channel's latest videos: the title and the description are the text (МВД puts its whole release there)."""
    d = json.loads(av.get(f"https://rutube.ru/api/video/person/{person}/?page=1&page_size={n}"))
    ch = channel(["rutube", person])
    return [_post(ch, v["title"] + ". " + TAGS.sub("", v.get("description") or ""), _utc(v["created_ts"]),
                  v["video_url"], f"rutube:{v['id']}", v.get("thumbnail_url") or "", official, v.get("hits") or 0)
            for v in d.get("results", [])]


def youtube(handle: str, n: int = 10, official: bool = False) -> list[dict]:
    """A YouTube channel's latest videos (yt-dlp, one metadata call a video: the description holds the release)."""
    url = f"https://www.youtube.com/{handle}/videos" if handle.startswith("@") else \
        f"https://www.youtube.com/channel/{handle}/videos"
    flat = json.loads(subprocess.run([*YTDLP, "--flat-playlist", "--playlist-end", str(n), "-J", url],
                                     capture_output=True, text=True, timeout=180, check=True).stdout)
    out = []
    for e in flat.get("entries") or []:
        f = CACHE / f"yt_{e['id']}.json"  # a video's metadata once: the cities and «Россия» all read НАК
        try:
            if f.is_file():
                v = json.loads(f.read_text(encoding="utf-8"))
            else:
                v = json.loads(subprocess.run([*YTDLP, "--skip-download", "-J", f"https://www.youtube.com/watch?v={e['id']}"],
                                              capture_output=True, text=True, timeout=120, check=True).stdout)
                v = {k: v.get(k) for k in ("title", "description", "timestamp", "upload_date", "webpage_url",
                                           "thumbnail", "view_count")}
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
        except (subprocess.SubprocessError, ValueError):
            continue
        ts = datetime.fromtimestamp(v["timestamp"], timezone.utc).isoformat() if v.get("timestamp") else \
            f"{v['upload_date'][:4]}-{v['upload_date'][4:6]}-{v['upload_date'][6:]}T12:00"
        out.append(_post(channel(["youtube", handle]), f"{v['title']}. {v.get('description') or ''}", _utc(ts),
                         v.get("webpage_url") or f"https://www.youtube.com/watch?v={e['id']}", f"youtube:{e['id']}",
                         v.get("thumbnail") or "", official, v.get("view_count") or 0))
    return out


def site(list_url: str, n: int = 15, official: bool = False, cache: Path = CACHE) -> list[dict]:
    """A news site's section: the article links on its list page (same section, a numeric id), each article read
    once in six hours (cache: an article changes little, but a later toll or a court date does come in)."""
    page = av.get(list_url)
    base = urlparse(list_url)
    links: list[str] = []
    for href in re.findall(r'href="([^"#?]+)"', page):
        u = urljoin(list_url, href)
        p = urlparse(u)
        if p.netloc == base.netloc and p.path.startswith(base.path.rstrip("/") + "/") \
                and re.search(r"/\d{5,}", p.path) and u not in links:  # an article id, not a page number («/news/3155/»)
            links.append(u)
    out = []
    for u in links[:n]:
        f = cache / (hashlib.md5(u.encode()).hexdigest()[:12] + ".json") if cache else None
        if f and f.is_file() and time.time() - f.stat().st_mtime < 6 * 3600:
            a = json.loads(f.read_text(encoding="utf-8"))
        else:
            try:
                html_ = av.get(u)
            except OSError:
                continue
            a = av.article(html_, u)
            rid = av.RUTUBE.search(html_)  # the player's id is enough: fetch_video asks Rutube only for a kept post
            src = None if rid else av.video_source(html_)
            a["video"] = f"rutube:{rid.group(1)}" if rid else (src[1] if src and src[0] == "mp4" else "")
            if f:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        dt = av.DATE.search(a.get("date") or "")
        if not dt or not a.get("text"):
            continue
        day, month, year, hm = dt.groups()
        mm = av.MONTHS.split("|").index(month.lower()) + 1
        out.append(_post(channel(["site", list_url]), f"{a['title']}. {a['text']}", _utc(f"{year}-{mm:02d}-{int(day):02d}T{int(hm.split(':')[0]) if hm else 12:02d}:{hm.split(':')[1] if hm else '00'}"),
                         u, a.get("video", ""), a.get("image") or "", official))
    return out


def posts(spec: list, cache: Path = CACHE) -> list[dict]:
    kind, ref, *rest = spec
    official = bool(rest[1]) if len(rest) > 1 else False
    if kind == "rutube":
        return rutube(ref, official=official)
    if kind == "youtube":
        return youtube(ref, official=official)
    if kind == "site":
        return site(ref, official=official, cache=cache)
    raise ValueError(f"unknown source kind {kind!r}")


def fetch_video(ref: str, dst: Path, max_height: int = 720) -> bool:
    """A post's video into dst (MP4): "rutube:<id>" by its HLS, "youtube:<id>" by yt-dlp, "tg:<channel>/<post>" by
    Telegram's API (tg_client.py, once the owner has logged in), else a direct link."""
    try:
        if ref.startswith("rutube:"):
            opts = json.loads(av.get(f"https://rutube.ru/api/play/options/{ref[7:]}/?format=json&no_404=true"))
            vs = av.variants(opts["video_balancer"]["m3u8"])
            url = max([v for v in vs if v[0] <= max_height] or vs)[1]
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-user_agent", av.UA, "-i", url, "-c", "copy",
                            "-movflags", "+faststart", str(dst)], check=True, timeout=900,
                           env={k: v for k, v in os.environ.items() if "proxy" not in k.lower()})  # Rutube: directly
        elif ref.startswith("tg:"):  # a Telegram video too big for the web preview: the app's API (tg_client.py)
            global _tg_left
            if _tg_left <= 0:  # a harvest's first run would pull dozens of big files: the freshest first, a few a run
                return False
            _tg_left -= 1
            ch, post = ref[3:].split("/")
            py = _VENV.parent / "python"
            return subprocess.run([str(py) if py.is_file() else sys.executable, str(Path(__file__).with_name("tg_client.py")),
                                   "fetch", ch, post, str(dst)], timeout=900).returncode == 0
        elif ref.startswith("youtube:"):
            subprocess.run([*YTDLP, "-f", f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/b[height<={max_height}]",
                            "--merge-output-format", "mp4", "-o", str(dst), f"https://www.youtube.com/watch?v={ref[8:]}"],
                           check=True, timeout=900)
        else:
            with open(dst, "wb") as f:
                f.write(av.get_bytes(ref))
        return dst.is_file() and dst.stat().st_size > 1024
    except (subprocess.SubprocessError, OSError, ValueError, KeyError):
        return False


if __name__ == "__main__":
    for p in posts(sys.argv[1:3] + [False, False]):
        print(p["dt"], p["ch"], p["mp4"] or "-", "|", p["text"][:150])
