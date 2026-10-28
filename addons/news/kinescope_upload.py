"""Upload a finished «ЧП» digest to Kinescope — every digest goes there right after the build (owner, 2026-10-28).

    .venv/bin/python addons/news/kinescope_upload.py projects/<slug> [--title "..."] [--replace]

The token and the target come from the environment (on the server: /opt/openmontage/.env): KINESCOPE_API_TOKEN and
KINESCOPE_PARENT_ID (a project or folder id). The token is never printed. The title defaults to
"<show> · <date> — <headline>" from story.json, the description to the stories' headlines. The result goes to
projects/<slug>/kinescope.json; a project that is already there is skipped, unless --replace (a rebuilt digest
replaces its old video and keeps the id).
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CITIES = json.loads((HERE / "cities.json").read_text(encoding="utf-8"))
HOST, PATH = "uploader.kinescope.io", "/v2/video"
API = "https://api.kinescope.io/v1/videos/"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--title")
    ap.add_argument("--replace", action="store_true", help="replace the video uploaded for this project before")
    a = ap.parse_args()
    token, parent = os.environ.get("KINESCOPE_API_TOKEN"), os.environ.get("KINESCOPE_PARENT_ID")
    if not token or not parent:
        sys.exit("KINESCOPE_API_TOKEN and KINESCOPE_PARENT_ID must be set")
    story = json.loads((a.project / "story.json").read_text(encoding="utf-8"))
    video = a.project / "renders" / "final.mp4"
    if not video.is_file():
        sys.exit(f"no render: {video}")
    done = a.project / "kinescope.json"
    old = json.loads(done.read_text(encoding="utf-8")) if done.is_file() else None
    if old and not a.replace:
        print(f"already on Kinescope: {old['id']} {old.get('play_link', '')} (use --replace after a rebuild)")
        return
    show = CITIES.get(story.get("city", ""), {}).get("show") or story.get("tag", "ЧП")
    title = a.title or f"{show} · {story.get('date', '')} — {story.get('headline', '')}".strip(" ·—")
    desc = "\n".join(f"{i}. {s.get('headline', '')} ({s.get('date', '')})" for i, s in enumerate(story.get("stories", []), 1))
    headers = {"Authorization": f"Bearer {token}", "X-Parent-ID": parent, "Content-Type": "video/mp4",
               # headers carry only ASCII; the Russian title and description follow as JSON once the id is known
               "X-Video-Title": a.project.name,
               "X-File-Name": f"{a.project.name}.mp4", "Content-Length": str(video.stat().st_size)}
    if old and a.replace:
        headers["X-Replace-Video-ID"] = old["id"]
    conn = http.client.HTTPSConnection(HOST, timeout=600)
    conn.putrequest("POST", PATH)
    for k, v in headers.items():
        conn.putheader(k, v)
    conn.endheaders()
    with video.open("rb") as f:
        while chunk := f.read(1 << 20):
            conn.send(chunk)
    r = conn.getresponse()
    body = r.read().decode("utf-8", "replace")
    if r.status >= 300:
        sys.exit(f"Kinescope upload failed: HTTP {r.status} {body[:300]}")
    data = json.loads(body).get("data", {})
    req = urllib.request.Request(API + data["id"], method="PATCH",
                                 data=json.dumps({"title": title[:250], "description": desc[:1000]}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=60).read()
    except Exception as e:  # the video is up anyway; only its title stays the slug
        print(f"title not set ({e}); the video is titled {a.project.name}", file=sys.stderr)
    out = {"id": data.get("id"), "title": title, "play_link": data.get("play_link") or f"https://kinescope.io/{data.get('id')}",
           "embed_link": data.get("embed_link") or f"https://kinescope.io/embed/{data.get('id')}"}
    done.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"KINESCOPE {out['id']} {out['play_link']}")


if __name__ == "__main__":
    main()
