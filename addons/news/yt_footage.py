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

The transcript is YouTube's own captions (the «Расшифровка видео» panel: a channel's or the automatic ones), fetched as
a small json3 file. Short news clips often have none — find --listen then downloads the clip (a few MB) and
transcribes its sound. The cut downloads the video with yt-dlp's own downloader into a cache and cuts locally: ffmpeg
streaming from googlevideo stalls through the egress (2026-10-30), yt-dlp's chunked download runs at ~9 MB/s.

Then a shot of it — credit «Видео: <канал> / YouTube» — and clean_footage.py as for any post's footage (every
channel's logo comes off, an agency's too). Prefer agencies, local TV (Татарстан 24, ТНВ,
Москва 24) and the event itself; a clip about another event is never this story's picture.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
_VENV = HERE.parents[1] / ".venv" / "bin" / "yt-dlp"
YTDLP = [str(_VENV) if _VENV.is_file() else "yt-dlp", "--quiet", "--no-warnings"]
CACHE = Path.home() / ".cache" / "openmontage" / "youtube"
CACHE_DAYS = 3
FORMAT = "bv*[height<=720][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]"


def url_of(ref: str) -> str:
    return ref if ref.startswith("http") else f"https://www.youtube.com/watch?v={ref}"


def search(query: str, n: int = 8, fresh: bool = False) -> list[dict]:
    out = subprocess.run([*YTDLP, "--flat-playlist", "-J", f"{'ytsearchdate' if fresh else 'ytsearch'}{n}:{query}"],
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


def cut(ref: str, t0: float, t1: float, dst: Path, tags: list[str] | None = None) -> Path:
    meta = info(ref)
    src = video(ref)
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0}", "-i", str(src), "-t", f"{t1 - t0}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "160k", "-movflags", "+faststart", str(dst)], check=True)
    said = " ".join(w for w, t in captions(ref) if t0 <= t < t1)
    channel = meta.get("channel") or meta.get("uploader") or ""
    out = {"source": meta.get("webpage_url"), "channel": channel, "title": meta.get("title"),
           "uploaded": meta.get("upload_date"), "from": t0, "to": t1, "said": said,
           "credit": f"Видео: {channel} / YouTube"}
    dst.with_suffix(".json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        sys.path.insert(0, str(HERE))
        import library

        library.add(dst, (tags or []) + ["youtube", channel], out["credit"], "YouTube", out["source"] or "")
    except OSError as e:
        print("library:", e, file=sys.stderr)
    return dst


def scout(query: str, words: str, n: int = 8, fresh: bool = False, lang: str = "ru") -> list[dict]:
    """Candidates ranked by their transcripts — which video says those words, where — before downloading any."""
    out = []
    for v in search(query, n, fresh):
        heard = captions(v["id"], lang)
        best = moments(heard, words, n=1) if heard else []
        out.append({**v, "captions": bool(heard), "moment": best[0] if best else None})
    return sorted(out, key=lambda v: -(v["moment"]["score"] if v["moment"] else -1 if not v["captions"] else 0))


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
    a = ap.parse_args()
    if a.cmd == "scout":
        for v in scout(a.query, a.words, a.n, a.fresh, a.lang):
            m = v["moment"]
            where = (f'{m["from"]:.0f}–{m["to"]:.0f}s [{m["score"]}] {m["text"][:110]}' if m else
                     "слова не звучат" if v["captions"] else "нет субтитров (find --listen)")
            print(f'{v["id"]}  {v["duration"]}s  {v["channel"]} | {v["title"][:70]}\n    {where}')
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
    else:
        print(cut(a.ref, a.t0, a.t1, a.dst, [t for t in a.tags.split(",") if t]))


if __name__ == "__main__":
    main()
