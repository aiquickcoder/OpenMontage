"""Footage from YouTube when a story's own pictures are short (owner 2026-10-30: «построй движок поиска футажей на
базе YouTube … ищешь нужное видео, смотришь расшифровку, которую даёт сам YouTube, далее скачиваешь правильный
момент, вырезаешь и вставляешь футаж, очищаешь при необходимости»).

    .venv/bin/python addons/news/yt_footage.py search "ДТП Лево-Булачная Казань" [--n 8] [--fresh]
        candidates: id, length, channel, title (--fresh: the newest first)
    .venv/bin/python addons/news/yt_footage.py find <id|url> "перекрёсток красный" [--n 5]
        YouTube's own transcript of the video (captions, Russian), the moments where those words are said
    .venv/bin/python addons/news/yt_footage.py cut <id|url> <from> <to> projects/<slug>/media/<name>.mp4 [--tags …]
        only that part (≤720p, cut on the second); <name>.json keeps the source, the channel, the times and what is
        said; the piece goes to the base of pictures (library.py) with the tags

Then a shot of it — credit «Видео: <канал> / YouTube» — and clean_footage.py as for any post's footage (other
channels' logos come off; an agency's own channel — "clean": "none"). Prefer agencies, local TV (Татарстан 24, ТНВ,
Москва 24) and the event itself; a clip about another event is never this story's picture. YouTube goes through the
factory egress (https_proxy); yt-dlp is in the server's .venv.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
_VENV = HERE.parents[1] / ".venv" / "bin" / "yt-dlp"
YTDLP = [str(_VENV) if _VENV.is_file() else "yt-dlp", "--quiet", "--no-warnings"]


def url_of(ref: str) -> str:
    return ref if ref.startswith("http") else f"https://www.youtube.com/watch?v={ref}"


def search(query: str, n: int = 8, fresh: bool = False) -> list[dict]:
    out = subprocess.run([*YTDLP, "--flat-playlist", "-J", f"{'ytsearchdate' if fresh else 'ytsearch'}{n}:{query}"],
                         capture_output=True, text=True, timeout=120, check=True).stdout
    return [{"id": e["id"], "duration": e.get("duration"), "channel": e.get("channel") or e.get("uploader") or "",
             "title": e.get("title") or ""} for e in json.loads(out).get("entries") or []]


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


def transcript(ref: str) -> list[tuple[str, float]]:
    with tempfile.TemporaryDirectory() as d:
        subprocess.run([*YTDLP, "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "ru.*,ru",
                        "--sub-format", "json3", "-o", f"{d}/v", url_of(ref)], capture_output=True, timeout=180)
        subs = sorted(Path(d).glob("v*.json3"), key=lambda f: ("orig" not in f.name, f.name))
        return words_of(json.loads(subs[0].read_text(encoding="utf-8"))) if subs else []


def cut(ref: str, t0: float, t1: float, dst: Path, tags: list[str] | None = None) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    meta = json.loads(subprocess.run([*YTDLP, "--skip-download", "-J", url_of(ref)], capture_output=True, text=True,
                                     timeout=120, check=True).stdout)
    subprocess.run([*YTDLP, "-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]/b", "--download-sections",
                    f"*{t0}-{t1}", "--force-keyframes-at-cuts", "--merge-output-format", "mp4", "-o", str(dst),
                    url_of(ref)], check=True, timeout=900)
    said = " ".join(w for w, t in transcript(ref) if t0 <= t < t1)
    info = {"source": meta.get("webpage_url"), "channel": meta.get("channel") or meta.get("uploader"),
            "title": meta.get("title"), "uploaded": meta.get("upload_date"), "from": t0, "to": t1, "said": said,
            "credit": f"Видео: {meta.get('channel') or meta.get('uploader')} / YouTube"}
    dst.with_suffix(".json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        sys.path.insert(0, str(HERE))
        import library

        library.add(dst, (tags or []) + ["youtube", info["channel"] or ""], info["credit"], "YouTube", info["source"] or "")
    except OSError as e:
        print("library:", e, file=sys.stderr)
    return dst


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s_ = sub.add_parser("search")
    s_.add_argument("query")
    s_.add_argument("--n", type=int, default=8)
    s_.add_argument("--fresh", action="store_true")
    f_ = sub.add_parser("find")
    f_.add_argument("ref")
    f_.add_argument("query")
    f_.add_argument("--n", type=int, default=5)
    c_ = sub.add_parser("cut")
    c_.add_argument("ref")
    c_.add_argument("t0", type=float)
    c_.add_argument("t1", type=float)
    c_.add_argument("dst", type=Path)
    c_.add_argument("--tags", default="")
    a = ap.parse_args()
    if a.cmd == "search":
        for v in search(a.query, a.n, a.fresh):
            print(f'{v["id"]}  {v["duration"]}s  {v["channel"]} | {v["title"]}')
    elif a.cmd == "find":
        words = transcript(a.ref)
        if not words:
            sys.exit("no transcript for this video")
        for m in moments(words, a.query, n=a.n):
            print(f'{m["from"]:7.1f}–{m["to"]:7.1f}  [{m["score"]}]  {m["text"][:160]}')
    else:
        print(cut(a.ref, a.t0, a.t1, a.dst, [t for t in a.tags.split(",") if t]))


if __name__ == "__main__":
    main()
