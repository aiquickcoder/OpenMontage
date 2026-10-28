"""Upload a finished «ЧП» digest to Kinescope — every digest goes there right after the build (owner, 2026-10-28).

    .venv/bin/python addons/news/kinescope_upload.py projects/<slug> [--title "..."] [--replace] [--notify]

The token and the target come from the environment (on the server: /opt/openmontage/.env): KINESCOPE_API_TOKEN and
KINESCOPE_PARENT_ID (a project or folder id). The token is never printed. The title defaults to
"<show> · <date> — <headline>" from story.json, the description to the stories' headlines. The result goes to
projects/<slug>/kinescope.json; a project that is already there is skipped, unless --replace (a rebuilt digest
replaces its old video and keeps the id). --notify waits for Kinescope to finish processing and sends the owner's
Telegram the title, the play link and a direct download link (the 1080p file: no expiry, no login; the original's
link lives an hour) — owner, 2026-10-28. `--links-only` sends that message for a video uploaded before.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CITIES = json.loads((HERE / "cities.json").read_text(encoding="utf-8"))
HOST, PATH = "uploader.kinescope.io", "/v2/video"
API = "https://api.kinescope.io/v1/videos/"


def download_link(token: str, vid: str, wait_s: int = 900) -> tuple[str, str]:
    """(status, link): the 1080p (else the best encoded) file's direct download link once Kinescope is done."""
    t0, data = time.time(), {}
    while time.time() - t0 < wait_s:
        req = urllib.request.Request(API + vid, headers={"Authorization": f"Bearer {token}"})
        data = json.loads(urllib.request.urlopen(req, timeout=60).read()).get("data", {})
        if data.get("status") == "done":
            break
        time.sleep(20)
    enc = [x for x in data.get("assets", []) if x.get("quality") != "original" and x.get("download_link")]
    enc.sort(key=lambda x: (x.get("quality") != "1080p", -(x.get("file_size") or 0)))
    return data.get("status", "?"), (enc[0]["download_link"] if enc else "")


def notify(proj: Path, out: dict, token: str) -> None:
    status, link = download_link(token, out["id"])
    out["download_link"] = link
    (proj / "kinescope.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    text = (f"🎬 {out['title']}\nСмотреть: {out['play_link']}\n"
            + (f"Скачать (1080p): {link}" if link else f"Скачать: ещё обрабатывается ({status}), ссылка будет в Kinescope"))
    repo = HERE.parents[1]
    subprocess.run([sys.executable, str(repo / "addons" / "mural" / "send_telegram.py"), text], cwd=repo,
                   env={**os.environ, "PYTHONPATH": str(repo)}, check=False)
    print(f"links sent: {link or status}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--title")
    ap.add_argument("--replace", action="store_true", help="replace the video uploaded for this project before")
    ap.add_argument("--notify", action="store_true", help="send the play + download links to the owner's Telegram")
    ap.add_argument("--links-only", action="store_true", help="only send the links of the video uploaded before")
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
    if a.links_only:
        if not old:
            sys.exit("not on Kinescope yet")
        notify(a.project, old, token)
        return
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
    print(f"KINESCOPE {out['id']} {out['play_link']}", flush=True)
    if a.notify:
        notify(a.project, out, token)


if __name__ == "__main__":
    main()
