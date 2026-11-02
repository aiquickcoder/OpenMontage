"""Footage from YouTube when a story's own pictures are short (owner 2026-10-30: «построй движок поиска футажей на
базе YouTube … ищешь нужное видео, смотришь расшифровку, которую даёт сам YouTube, далее скачиваешь правильный
момент, вырезаешь и вставляешь футаж, очищаешь при необходимости»). Everything up to the cut is decided before any
video is downloaded: the words from YouTube's own transcript, the picture from its storyboard.

    .venv/bin/python addons/news/yt_footage.py scout "ДТП Лево-Булачная Казань" "перекрёсток красный" [--n 8] [--fresh]
        [--lang pl]
        search + every candidate's transcript (no video downloaded): ranked by where the words are said
    .venv/bin/python addons/news/yt_footage.py search "<query>" [--n 8] [--fresh]
        candidates only: id, length, channel, title (--fresh: the newest first)
    .venv/bin/python addons/news/yt_footage.py find <id|url> "перекрёсток красный" [--n 5] [--listen] [--lang pl]
        one video's transcript, the moments where those words are said (--listen: no captions — its sound, transcribed)
    .venv/bin/python addons/news/yt_footage.py peek <id|url> <from> <to> <sheet.jpg>
        what is on screen then: YouTube's storyboard (the scrubbing previews, ≈1 a second) as a contact sheet
    .venv/bin/python addons/news/yt_footage.py cut <id|url> <from> <to> projects/<slug>/media/<name>.mp4 [--tags …]
        only that part (≤720p, H.264); <name>.json keeps the source, the channel, the times and what is said; the
        piece goes to the base of pictures (library.py) with the tags
    .venv/bin/python addons/news/yt_footage.py tt-search "Frankfurt Demo Polizei" [--n 8] [--when THIS_WEEK]
        [--sort RELEVANCE|MOST_LIKED|DATE_POSTED]   TikTok through Apify: url, length, views, date, @author, caption
    .venv/bin/python addons/news/yt_footage.py tt-peek <tiktok url> <sheet.jpg>
        the whole video as a contact sheet, a frame a second (a longer one than 60 s: every n s, printed) — pick a
        piece without the author's
        burned-in captions
    .venv/bin/python addons/news/yt_footage.py tt-cut <tiktok url> <from> <to> projects/<slug>/media/tt/<name>.mp4
        ≤ 4 s, no sound, ~4 % cut off each side (92 % of each dimension kept), no credit anywhere (the author and the
        link stay with us: <name>.json and the base of pictures)

The transcript is YouTube's own captions (the «Расшифровка видео» panel: a channel's or the automatic ones), fetched as
a small json3 file. Short news clips often have none — find --listen then downloads the clip (a few MB) and
transcribes its sound. The cut downloads the video with yt-dlp's own downloader into a cache and cuts locally: ffmpeg
streaming from googlevideo stalls through the egress (2026-10-30), yt-dlp's chunked download runs at ~9 MB/s.

Then a shot of it — credit «Видео: <канал> / YouTube» — and clean_footage.py as for any post's footage (every
channel's logo comes off, an agency's too). A story with no footage of its own (owner 2026-10-31) is told with thematic
footage found here: the agent decides per phrase what to look for — the people, companies, products and places it
names, TV reports on the same phenomenon — and writes the queries itself (README «Новость без своих кадров»). Prefer agencies, local TV (Татарстан 24, ТНВ,
Москва 24) and the event itself; a clip about another event is never this story's picture.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
_VENV = HERE.parents[1] / ".venv" / "bin" / "yt-dlp"
YTDLP = [str(_VENV) if _VENV.is_file() else "yt-dlp", "--quiet", "--no-warnings"]
CACHE = Path.home() / ".cache" / "openmontage" / "youtube"
CACHE_DAYS = 3
# the YouTube exits in order: factory-yt-proxy (the Hostkey NL server — 01.11 YouTube asked every other exit of the
# server to «sign in to confirm you're not a bot»), then the factory's general egress
EGRESSES = [os.environ["YT_PROXY"]] if os.environ.get("YT_PROXY") else ["http://127.0.0.1:10811", "http://127.0.0.1:10809"]


def _up(url: str) -> bool:
    import socket

    host, port = url.rsplit("://", 1)[-1].rsplit(":", 1)
    try:
        socket.create_connection((host, int(port)), timeout=1).close()
        return True
    except OSError:
        return False


# YouTube does not answer the server directly: yt-dlp and the storyboard fetches go through the first exit that listens
# unless the caller set a proxy (31.10: run with only the .env, every search failed on an SSL EOF); on a machine
# without one (the Mac) nothing changes
EGRESS = next((u for u in EGRESSES if _up(u)), "")
if "https_proxy" not in os.environ and EGRESS:
    os.environ["https_proxy"] = EGRESS
# a silent upload (drone footage often has no sound) has no audio to merge: then the picture alone — footage is muted
FORMAT = ("bv*[height<=720][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]"
          "/bv*[height<=720][vcodec^=avc1]/bv*[height<=720]")


def url_of(ref: str) -> str:
    return ref if ref.startswith("http") else f"https://www.youtube.com/watch?v={ref}"


def search(query: str, n: int = 8, fresh: bool = False) -> list[dict]:
    # the newest first: YouTube's own results page sorted by upload date (yt-dlp 2026.10.26's ytsearchdate gives null)
    src = (f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}&sp=CAI%253D" if fresh
           else f"ytsearch{n}:{query}")
    out = subprocess.run([*YTDLP, "--flat-playlist", "-J", "--playlist-end", str(n), src],
                         capture_output=True, text=True, timeout=120, check=True).stdout
    return [{"id": e["id"], "duration": e.get("duration"), "channel": e.get("channel") or e.get("uploader") or "",
             "title": e.get("title") or ""} for e in json.loads(out).get("entries") or []]


@lru_cache(maxsize=32)
def info(ref: str) -> dict:
    return json.loads(subprocess.run([*YTDLP, "--skip-download", "-J", url_of(ref)], capture_output=True, text=True,
                                     timeout=120, check=True).stdout)


def words_of(json3: dict) -> list[tuple[str, float]]:
    """YouTube's json3 captions as (word, start seconds)."""
    out = []
    for ev in json3.get("events", []):
        t0 = ev.get("tStartMs", 0) / 1000
        for seg in ev.get("segs") or []:
            for w in (seg.get("utf8") or "").split():
                out.append((w, round(t0 + seg.get("tOffsetMs", 0) / 1000, 2)))
    return out


def _stem(w: str) -> str:
    return re.sub(r"[^а-яёa-z0-9]", "", w.lower().replace("ё", "е"))[:5]


def moments(words: list[tuple[str, float]], query: str, window: float = 8.0, n: int = 5) -> list[dict]:
    """Where the query's words are said together: every place a query word starts a window, scored by how many of
    the query's different words fall inside it; overlapping windows keep the best."""
    want = {_stem(w) for w in query.split() if len(_stem(w)) >= 3}
    hits = []
    for i, (w, t) in enumerate(words):
        if _stem(w) not in want:
            continue
        inside = [(x, s) for x, s in words[i:] if s < t + window]
        got = {_stem(x) for x, _ in inside} & want
        hits.append({"from": max(0.0, round(t - 1.0, 1)), "to": round(t + window, 1), "score": len(got),
                     "text": " ".join(x for x, _ in inside)})
    best = []
    for h in sorted(hits, key=lambda h: (-h["score"], h["from"])):
        if all(h["to"] <= b["from"] or h["from"] >= b["to"] for b in best):
            best.append(h)
    return best[:n]


def captions(ref: str, lang: str = "ru") -> list[tuple[str, float]]:
    """YouTube's own transcript, nothing else downloaded: the channel's captions in that language (a foreign story —
    its country's TV in its own: "pl" for Poland), else the automatic ones."""
    with tempfile.TemporaryDirectory() as d:
        subprocess.run([*YTDLP, "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", f"{lang}.*,{lang}",
                        "--sub-format", "json3", "-o", f"{d}/v", url_of(ref)], capture_output=True, timeout=180)
        subs = sorted(Path(d).glob("v*.json3"), key=lambda f: ("orig" not in f.name, f.name))
        return words_of(json.loads(subs[0].read_text(encoding="utf-8"))) if subs else []


def video(ref: str) -> Path:
    """The whole video (≤720p) in the cache — yt-dlp's chunked download through the egress; older ones are dropped."""
    CACHE.mkdir(parents=True, exist_ok=True)
    for old in CACHE.glob("*.mp4"):
        if time.time() - old.stat().st_mtime > CACHE_DAYS * 86400:
            old.unlink(missing_ok=True)
    vid = info(ref)["id"]
    dst = CACHE / f"{vid}.mp4"
    if not dst.is_file():
        subprocess.run([*YTDLP, "-f", FORMAT, "--merge-output-format", "mp4", "-o", str(dst), url_of(ref)],
                       check=True, timeout=1800)
    return dst


def listen(ref: str) -> list[tuple[str, float]]:
    """No captions: the clip's sound, transcribed here (whisper)."""
    sys.path.insert(0, str(HERE.parents[1]))
    from addons.news.build import transcribe

    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "a.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video(ref)), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                       check=True)
        return [(w["word"].strip(), round(w["start"], 2)) for w in transcribe(wav)]


def peek(ref: str, t0: float, t1: float, dst: Path, cols: int = 4, most: int = 16) -> Path:
    """What is on screen from t0 to t1, nothing downloaded but YouTube's storyboard: its largest previews cut into
    tiles, at most `most` of them evenly over the moment, each with its time."""
    from PIL import Image, ImageDraw

    boards = [f for f in info(ref)["formats"] if f.get("format_note") == "storyboard" and f.get("fragments")]
    if not boards:
        sys.exit("no storyboard for this video")
    sb = max(boards, key=lambda f: f.get("width") or 0)
    per = sb["rows"] * sb["columns"]
    tiles, t = [], 0.0
    for frag in sb["fragments"]:
        d = frag.get("duration") or per / (sb.get("fps") or 1)
        if t < t1 and t + d > t0:
            with urllib.request.urlopen(frag["url"], timeout=60) as r:  # https_proxy from the env: the egress
                img = Image.open(io.BytesIO(r.read())).convert("RGB")
            for k in range(per):
                ts = t + k * d / per
                x, y = (k % sb["columns"]) * sb["width"], (k // sb["columns"]) * sb["height"]
                if t0 <= ts < t1 and y + sb["height"] <= img.height:
                    tiles.append((ts, img.crop((x, y, x + sb["width"], y + sb["height"]))))
        t += d
    if not tiles:
        sys.exit(f"nothing in {t0}–{t1} s (the video is {t:.0f} s)")
    tiles = tiles[::max(1, -(-len(tiles) // most))]
    rows = -(-len(tiles) // cols)
    sheet = Image.new("RGB", (cols * sb["width"], rows * sb["height"]), "black")
    for i, (ts, tile) in enumerate(tiles):
        x, y = (i % cols) * sb["width"], (i // cols) * sb["height"]
        sheet.paste(tile, (x, y))
        ImageDraw.Draw(sheet).text((x + 4, y + 2), f"{ts:.0f}s", fill="yellow")
    dst.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dst, quality=90)
    return dst


def scenes(path: Path) -> list[float]:
    """Where a piece's own scenes change (ffmpeg scene score > 0.3), in seconds from its start."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "select=gt(scene\\,0.3),showinfo", "-an",
                        "-f", "null", "-"], capture_output=True, text=True)
    return [round(float(x), 2) for x in re.findall(r"pts_time:([0-9.]+)", r.stderr)]


def _dhash(path: Path, t: float) -> int:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1", "-vf",
                          "scale=9:8,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    if len(raw) < 72:
        return -1
    px = [[raw[r * 9 + c] for c in range(9)] for r in range(8)]
    return sum(1 << (r * 8 + c) for r in range(8) for c in range(8) if px[r][c] > px[r][c + 1])


def repeats(path: Path, cuts: list[float], most: int = 10) -> list[tuple[float, float]]:
    """Scenes of a piece that look like an earlier one of it (a compilation of alike takes): (earlier, later) starts."""
    starts = [0.0, *cuts]
    marks = [(s, _dhash(path, s + 0.3)) for s in starts]
    out = []
    for i, (b, hb) in enumerate(marks):
        for a, ha in marks[:max(0, i - 1)]:  # not the scene right before: a cut inside one shot looks alike anyway
            if ha >= 0 and hb >= 0 and bin(ha ^ hb).count("1") <= most:
                out.append((a, b))
                break
    return out


def cut(ref: str, t0: float, t1: float, dst: Path, tags: list[str] | None = None) -> Path:
    meta = info(ref)
    src = video(ref)
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0}", "-i", str(src), "-t", f"{t1 - t0}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "160k", "-map_metadata", "-1", "-map_metadata:s", "-1", "-map_chapters", "-1",
                    "-movflags", "+faststart", str(dst)], check=True)
    said = " ".join(w for w, t in captions(ref) if t0 <= t < t1)
    channel = meta.get("channel") or meta.get("uploader") or ""
    cuts = scenes(dst)
    again = repeats(dst, cuts)
    if again:  # 01.11: «4 videos» of hands counting euros — two alike takes in one piece looked like the same shot twice
        print(f"⚠ {dst.name}: a scene repeats — {', '.join(f'{b:.1f} s looks like {a:.1f} s' for a, b in again)}; "
              f"cut inside one scene (scenes at {', '.join(f'{c:.1f}' for c in cuts)} s)", file=sys.stderr, flush=True)
    out = {"source": meta.get("webpage_url"), "channel": channel, "title": meta.get("title"),
           "uploaded": meta.get("upload_date"), "from": t0, "to": t1, "said": said,
           "credit": f"Видео: {channel} / YouTube", "scenes": cuts, "repeats": again}
    dst.with_suffix(".json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        sys.path.insert(0, str(HERE))
        import library

        library.add(dst, (tags or []) + ["youtube", channel], out["credit"], "YouTube", out["source"] or "")
    except OSError as e:
        print("library:", e, file=sys.stderr)
    return dst


# a staged or generated picture is never a story's footage (31.10: «Lądowanie w Warszawie 30.10.2026» was X-Plane 12,
# «…makes a sudden U-turn over Spain» a content farm's animated map)
# (not a bare «AI»: a story about AI agents has it in every title — 31.10, the Lindy demos)
STAGED = re.compile(r"inscenizac|symulac|symulator|x-?plane|msfs|flight ?sim|simulat|reconstruct|rekonstrukc|"
                    r"\bAI[- ](?:generated|animation|animated|video)|(?:generated|made|created) (?:by|with|using) AI|"
                    r"wygenerowan|нейросет\w* (?:сгенер|созда|нарисова)|симуляц|реконструкц|сгенерирова", re.I)


def staged(ref: str) -> bool:
    """The title or description says it is a simulation, a reconstruction or generated."""
    try:
        d = info(ref)
    except subprocess.SubprocessError:
        return False
    return bool(STAGED.search(f"{d.get('title', '')}\n{d.get('description', '')}"))


def scout(query: str, words: str, n: int = 8, fresh: bool = False, lang: str = "ru") -> list[dict]:
    """Candidates ranked by their transcripts — which video says those words, where — before downloading any."""
    out = []
    for v in search(query, n, fresh):
        heard = captions(v["id"], lang)
        best = moments(heard, words, n=1) if heard else []
        out.append({**v, "captions": bool(heard), "moment": best[0] if best else None, "staged": staged(v["id"])})
    return sorted(out, key=lambda v: -(v["moment"]["score"] if v["moment"] else -1 if not v["captions"] else 0))


# -- TikTok -------------------------------------------------------------------------------------------------------------
# Owner 2026-11-02: «а что если находить так футажи нам нужные?» — yes, with «самое критичное 3-е»: TikTok knows its
# own videos and cuts the reach of «unoriginal» re-uploads. So every TikTok piece is ≤ 4 s, without its sound, its
# edges cropped 8 %, inside our layout among other sources; a film takes ≤ 3 of them and never opens on one (build.py
# refuses), no credit on it; official media accounts (hessenschau, ITV…) before eyewitnesses, and an eyewitness only when the date and
# the place match the agencies. Search through Apify (APIFY_TOKEN, the actor the viral pick uses); the file comes from
# the CDN link Apify returns — yt-dlp cannot reach TikTok from the server (02.11). A month cap keeps the spend in the
# owner's Apify plan (with the pick's TikTok look: ≈ $4.4 of the free $5).
TT_ACTOR = "apidojo~tiktok-scraper-api"
TT_MAX_PIECE = 4.0
TT_CROP = 0.92
TT_CACHE = CACHE.parent / "tiktok"


def _apify(payload: dict, timeout: int = 150) -> list[dict]:
    token = os.environ.get("APIFY_TOKEN", "")
    if not token:
        sys.exit("no APIFY_TOKEN in the environment (/opt/openmontage/.env)")
    req = urllib.request.Request(
        f"https://api.apify.com/v2/acts/{TT_ACTOR}/run-sync-get-dataset-items?timeout={timeout}",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout + 30) as r:
        items = json.loads(r.read())
    if isinstance(items, list) and items and all(isinstance(it, dict) and it.get("demo") for it in items):
        # the actor's free-plan quota is spent (5 runs a month on Apify FREE): it answers {"demo": true} rows — never
        # an empty «success» (the agent's self-test, 02.11)
        sys.exit("Apify: the actor answers demo rows — its free-plan quota is spent (a paid plan needed)")
    return [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []


def _tt_spend(n: int = 1) -> None:
    """Count this month's TikTok searches; stop past NEWS_TIKTOK_FOOTAGE_MONTH (default 120 ≈ $1)."""
    TT_CACHE.mkdir(parents=True, exist_ok=True)
    f = TT_CACHE / "month.json"
    month = time.strftime("%Y-%m", time.gmtime())
    try:
        c = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        c = {}
    if c.get("month") != month:
        c = {"month": month, "queries": 0}
    if c["queries"] + n > int(os.environ.get("NEWS_TIKTOK_FOOTAGE_MONTH", "120")):
        sys.exit(f"the month's TikTok searches are spent ({c['queries']}); use YouTube or the agencies")
    c["queries"] += n
    f.write_text(json.dumps(c), encoding="utf-8")


def _tt_id(url: str) -> str:
    m = re.search(r"/video/(\d+)", url)
    return m.group(1) if m else re.sub(r"\W+", "_", url)[-40:]


def _tt_row(it: dict) -> dict:
    ch = it.get("channel") if isinstance(it.get("channel"), dict) else {}
    v = it.get("video") if isinstance(it.get("video"), dict) else {}

    def num(x) -> int:
        try:
            return int(float(x or 0))
        except (TypeError, ValueError):
            return 0

    return {"url": str(it.get("postPage") or ""), "id": str(it.get("id") or ""), "author": str(ch.get("username") or ""),
            "name": str(ch.get("name") or ""), "verified": bool(ch.get("verified")), "views": num(it.get("views")),
            "ts": num(it.get("uploadedAt")), "duration": float(v.get("duration") or 0),
            "title": " ".join(str(it.get("title") or "").split()), "video": str(v.get("url") or "")}


def tt_search(query: str, n: int = 8, sort: str = "RELEVANCE", when: str = "THIS_WEEK") -> list[dict]:
    _tt_spend()
    rows = [_tt_row(it) for it in _apify({"keywords": [query], "maxItems": n, "dateRange": when, "sortType": sort,
                                          "includeSearchKeywords": True})]
    TT_CACHE.mkdir(parents=True, exist_ok=True)
    for r in rows:
        if r["url"]:
            (TT_CACHE / f"{_tt_id(r['url'])}.json").write_text(json.dumps({**r, "fetched": time.time()},
                                                                         ensure_ascii=False), encoding="utf-8")
    return rows


def tt_item(url: str, fresh: bool = False) -> dict:
    """The video's row: from the search cache while its CDN link is young (< 3 h), else asked again."""
    f = TT_CACHE / f"{_tt_id(url)}.json"
    try:
        row = json.loads(f.read_text(encoding="utf-8"))
        if not fresh and time.time() - row.get("fetched", 0) < 3 * 3600 and row.get("video"):
            return row
    except (OSError, ValueError):
        pass
    _tt_spend()
    rows = [_tt_row(it) for it in _apify({"startUrls": [url], "maxItems": 1})]
    if not rows or not rows[0]["video"]:
        sys.exit(f"TikTok: no video for {url}")
    TT_CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({**rows[0], "fetched": time.time()}, ensure_ascii=False), encoding="utf-8")
    return {**rows[0], "fetched": time.time()}


def tt_video(url: str) -> Path:
    dst = TT_CACHE / f"{_tt_id(url)}.mp4"
    if dst.is_file() and dst.stat().st_size > 10_000:
        return dst
    for fresh in (False, True):
        row = tt_item(url, fresh)
        for px in [None] + EGRESSES:
            opener = urllib.request.build_opener(*([urllib.request.ProxyHandler({"https": px, "http": px})] if px else []))
            try:
                req = urllib.request.Request(row["video"], headers={"User-Agent": "Mozilla/5.0"})
                with opener.open(req, timeout=120) as r:
                    data = r.read()
                if len(data) > 10_000:
                    dst.write_bytes(data)
                    return dst
            except OSError:
                continue
    sys.exit(f"TikTok: could not download {url}")


def tt_peek(url: str, sheet: Path) -> Path:
    """The whole video on one sheet: a frame a second up to 60 s, a longer one every `step` s (printed)."""
    src = tt_video(url)
    sheet.parent.mkdir(parents=True, exist_ok=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(src)], capture_output=True, text=True).stdout.strip() or 0)
    step = max(1, -(-int(dur) // 60))
    rows = max(1, -(-int(dur / step + 1) // 6))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", f"fps=1/{step},scale=180:-2,tile=6x{rows}",
                    "-frames:v", "1", str(sheet)], check=True)
    print(f"{dur:.1f} s: tile i (left to right, top to bottom, from 0) = second i×{step}", file=sys.stderr)
    return sheet


def tt_cut(url: str, t0: float, t1: float, dst: Path, tags: list[str] | None = None) -> Path:
    if t1 - t0 > TT_MAX_PIECE + 1e-6:
        sys.exit(f"a TikTok piece is ≤ {TT_MAX_PIECE:g} s (owner 2026-11-02: TikTok cuts the reach of re-uploads) — "
                 f"{t1 - t0:.1f} s asked")
    if dst.parent.name != "tt":  # build.py counts the TikTok pieces by this folder
        dst = dst.parent / "tt" / dst.name
    row, src = tt_item(url), tt_video(url)
    dst.parent.mkdir(parents=True, exist_ok=True)
    crop = f"crop=trunc(iw*{TT_CROP}/2)*2:trunc(ih*{TT_CROP}/2)*2"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0}", "-i", str(src), "-t", f"{t1 - t0}", "-vf", crop,
                    "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-map_metadata", "-1", "-map_metadata:s", "-1", "-map_chapters", "-1",  # TikTok's vid / vid_md5 tags
                    "-movflags", "+faststart", str(dst)], check=True)
    who = f"@{row['author']}" if row.get("author") else (row.get("name") or "TikTok")
    # no credit on a TikTok piece, on screen or in the post (owner 2026-11-02: «давай не ставить подпись») — the
    # author and the link stay here, for us
    out = {"source": url, "channel": who, "name": row.get("name"), "title": row.get("title"),
           "uploaded": time.strftime("%Y-%m-%d", time.gmtime(row["ts"])) if row.get("ts") else None,
           "views": row.get("views"), "from": t0, "to": t1, "credit": "", "author": who, "tiktok": True}
    dst.with_suffix(".json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        sys.path.insert(0, str(HERE))
        import library

        library.add(dst, (tags or []) + ["tiktok", who], f"TikTok {who}", "TikTok", url)
    except OSError as e:
        print("library:", e, file=sys.stderr)
    return dst


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    o_ = sub.add_parser("scout")
    o_.add_argument("query")
    o_.add_argument("words")
    o_.add_argument("--n", type=int, default=8)
    o_.add_argument("--fresh", action="store_true")
    o_.add_argument("--lang", default="ru")
    s_ = sub.add_parser("search")
    s_.add_argument("query")
    s_.add_argument("--n", type=int, default=8)
    s_.add_argument("--fresh", action="store_true")
    f_ = sub.add_parser("find")
    f_.add_argument("ref")
    f_.add_argument("query")
    f_.add_argument("--n", type=int, default=5)
    f_.add_argument("--listen", action="store_true")
    f_.add_argument("--lang", default="ru")
    p_ = sub.add_parser("peek")
    p_.add_argument("ref")
    p_.add_argument("t0", type=float)
    p_.add_argument("t1", type=float)
    p_.add_argument("dst", type=Path)
    c_ = sub.add_parser("cut")
    c_.add_argument("ref")
    c_.add_argument("t0", type=float)
    c_.add_argument("t1", type=float)
    c_.add_argument("dst", type=Path)
    c_.add_argument("--tags", default="")
    ts_ = sub.add_parser("tt-search")
    ts_.add_argument("query")
    ts_.add_argument("--n", type=int, default=8)
    ts_.add_argument("--sort", default="RELEVANCE", choices=["RELEVANCE", "MOST_LIKED", "DATE_POSTED"])
    ts_.add_argument("--when", default="THIS_WEEK", choices=["YESTERDAY", "THIS_WEEK", "THIS_MONTH", "ALL_TIME"])
    tp_ = sub.add_parser("tt-peek")
    tp_.add_argument("url")
    tp_.add_argument("dst", type=Path)
    tc_ = sub.add_parser("tt-cut")
    tc_.add_argument("url")
    tc_.add_argument("t0", type=float)
    tc_.add_argument("t1", type=float)
    tc_.add_argument("dst", type=Path)
    tc_.add_argument("--tags", default="")
    a = ap.parse_args()
    if a.cmd == "scout":
        for v in scout(a.query, a.words, a.n, a.fresh, a.lang):
            m = v["moment"]
            where = (f'{m["from"]:.0f}–{m["to"]:.0f}s [{m["score"]}] {m["text"][:110]}' if m else
                     "слова не звучат" if v["captions"] else "нет субтитров (find --listen)")
            flag = "  ✗ ИНСЦЕНИРОВКА / ИИ — не брать" if v["staged"] else ""
            print(f'{v["id"]}  {v["duration"]}s  {v["channel"]} | {v["title"][:70]}{flag}\n    {where}')
    elif a.cmd == "search":
        for v in search(a.query, a.n, a.fresh):
            print(f'{v["id"]}  {v["duration"]}s  {v["channel"]} | {v["title"]}')
    elif a.cmd == "find":
        words = captions(a.ref, a.lang) or (listen(a.ref) if a.listen and a.lang == "ru" else [])
        if not words:
            sys.exit("no captions for this video (--listen transcribes its sound)")
        for m in moments(words, a.query, n=a.n):
            print(f'{m["from"]:7.1f}–{m["to"]:7.1f}  [{m["score"]}]  {m["text"][:160]}')
    elif a.cmd == "peek":
        print(peek(a.ref, a.t0, a.t1, a.dst))
    elif a.cmd == "tt-search":
        for v in tt_search(a.query, a.n, a.sort, a.when):
            day = time.strftime("%d.%m %H:%M", time.gmtime(v["ts"] + 3 * 3600)) if v["ts"] else "?"
            print(f'{v["url"]}  {v["duration"]:.0f}s  {v["views"]:,} просм.  {day}  @{v["author"]} ({v["name"]})'
                  f'{" ✓" if v["verified"] else ""}\n    {v["title"][:150]}')
    elif a.cmd == "tt-peek":
        print(tt_peek(a.url, a.dst))
    elif a.cmd == "tt-cut":
        print(tt_cut(a.url, a.t0, a.t1, a.dst, [t for t in a.tags.split(",") if t]))
    else:
        print(cut(a.ref, a.t0, a.t1, a.dst, [t for t in a.tags.split(",") if t]))


if __name__ == "__main__":
    main()
