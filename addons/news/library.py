"""The base of real pictures (owner 2026-10-30: no AI pictures at all — «если нам чего-то не хватает, давай думать,
где можем это достать… ищем в интернете необходимую информацию, чтобы использовать в кадре, и запоминаем базу»).

Every real picture found for a story — a Commons photo of the place, an agency's photo or video, a news site's
photo — goes here with tags (city, place, agency, what is in it), credit, licence and source, so the next story about
the same airport, court or highway starts from it.

    python3 -I addons/news/library.py add <file> --tags "Казань,аэропорт,терминал" --credit "Фото: …" \
        --license "CC BY-SA 4.0" --source <url>
    python3 -I addons/news/library.py find казань аэропорт          # best matches first
    python3 -I addons/news/library.py copy <id> projects/<slug>/media/

Where: NEWS_LIBRARY, else /srv/factory/news_library on the server, else ~/memes/news_library: files/<sha1>.<ext> and
index.json. Stdlib only (the harvesters and fetch_commons.py run on the system python).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path


def root() -> Path:
    if os.environ.get("NEWS_LIBRARY"):
        return Path(os.environ["NEWS_LIBRARY"])
    srv = Path("/srv/factory")
    return srv / "news_library" if srv.is_dir() else Path.home() / "memes" / "news_library"


def _index(r: Path) -> list[dict]:
    f = r / "index.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []


def _norm(tag: str) -> str:
    return tag.strip().lower().replace("ё", "е")


def add(file: Path, tags: list[str], credit: str = "", license: str = "", source: str = "", r: Path | None = None) -> dict:
    """Store a picture once (by its content); a second add merges the tags."""
    r = r or root()
    (r / "files").mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha1(file.read_bytes()).hexdigest()[:16]
    dst = r / "files" / f"{sha}{file.suffix.lower()}"
    if not dst.is_file():
        shutil.copy2(file, dst)
    idx = _index(r)
    e = next((x for x in idx if x["id"] == sha), None)
    if e:
        e["tags"] = sorted(set(e["tags"]) | {_norm(t) for t in tags if t.strip()})
    else:
        e = {"id": sha, "file": dst.name, "name": file.name, "tags": sorted({_norm(t) for t in tags if t.strip()}),
             "credit": credit, "license": license, "source": source, "added": date.today().isoformat()}
        idx.append(e)
    (r / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    return e


def find(words: list[str], r: Path | None = None, n: int = 20) -> list[dict]:
    """Entries ranked by how many of the words their tags or name contain (a word may be a tag's start: «аэропорт»
    finds «аэропорт казань»), the newest first among equals."""
    want = [_norm(w) for w in words if w.strip()]
    scored = []
    for e in _index(r or root()):
        hay = " ".join(e["tags"]) + " " + _norm(e.get("name", ""))
        k = sum(w in hay for w in want)
        if k:
            scored.append((k, e.get("added", ""), e))
    return [e for _, _, e in sorted(scored, key=lambda x: (x[0], x[1]), reverse=True)[:n]]


def copy(entry_id: str, dst_dir: Path, r: Path | None = None) -> Path:
    r = r or root()
    e = next(x for x in _index(r) if x["id"] == entry_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    out = dst_dir / e["name"]
    shutil.copy2(r / "files" / e["file"], out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a_ = sub.add_parser("add")
    a_.add_argument("file", type=Path)
    a_.add_argument("--tags", required=True)
    a_.add_argument("--credit", default="")
    a_.add_argument("--license", default="")
    a_.add_argument("--source", default="")
    f_ = sub.add_parser("find")
    f_.add_argument("words", nargs="+")
    c_ = sub.add_parser("copy")
    c_.add_argument("id")
    c_.add_argument("dst", type=Path)
    a = ap.parse_args()
    if a.cmd == "add":
        e = add(a.file, a.tags.split(","), a.credit, a.license, a.source)
        print(e["id"], ", ".join(e["tags"]))
    elif a.cmd == "find":
        for e in find(a.words):
            print(f'{e["id"]}  {e["name"]}  [{", ".join(e["tags"])}]  {e["credit"]} {e["license"]}')
    else:
        print(copy(a.id, a.dst))


if __name__ == "__main__":
    sys.exit(main())
