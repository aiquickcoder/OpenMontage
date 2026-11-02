"""Everything after story.json on the server, unattended: pictures and clean footage in parallel, build, deliver.

    systemd-run --wait --pipe --uid=factory -p EnvironmentFile=/opt/openmontage/.env \
        -p EnvironmentFile=/opt/watermark-cleaner/deploy/.env -E HOME=/home/factory --working-directory=/opt/openmontage \
        .venv/bin/python addons/news/produce.py projects/<slug> [--caption caption.txt] [--send] [--kinescope]

story.json must already have its voice (build.py --voice-only), its shots and its "illustrations"; hand boxes for
still cameras go into media/wm.json before this (see addons/news/README.md «Очистка футажей»). Steps:
1. Flow egress: the slower of FLOW_PROXY (US tunnel) and the reserve exit loses — the US tunnel crawled at ~100 KB/s
   on 2026-10-27 and Flow never loaded through it.
2. In parallel: gen_shots.py (Flow, the browser) and clean_footage.py --run (the watermark-cleaner, CPU).
3. build.py --retime. Steps 2–4 run under one lock shared by every project (owner 2026-11-01: one heavy job at a time
   on this server — two films cleaning and rendering at once drove the load to ~300 and took sshd down); a second
   produce.py waits for the first, and a render or a cleaning started by hand (build.py, clean_footage.py --run)
   is waited for too.
4. A digest also gives one short per story for TikTok (build.py --split: 15–25 s, the hook in the first frame, a loop,
   no cat by default — owner 2026-10-30, the TikTok review); "tiktok": {"split": false} in story.json skips them.
5. --send: the render to the owner's Telegram (caption from --caption; a viral TikTok film's caption opens with the
   account it is for and when to post it — tiktok_accounts.json), then the shorts; --kinescope:
   kinescope_upload.py for the digest, --replace when the project was uploaded before.
The last line is PRODUCE_RESULT: READY|FAILED <project> <reason>.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = str(REPO / ".venv" / "bin" / "python")
RESERVE_FLOW_PROXY = "socks5://127.0.0.1:20900"  # the pinned xray exit: images work through it, Veo does not
BUILD_LOCK = Path("/tmp/news_build.lock")
# a film's heavy work outside produce.py's lock: a render (build.py without --voice-only / --storyboard), cleaning
# (clean_footage.py --run) — an agent may run them by hand; and another film's produce.py (for morning.py)
RENDER_OR_CLEAN = r"addons/news/build\.py(?![^\n]*--(?:voice-only|storyboard))|clean_footage\.py[^\n]*--run"
HEAVY = re.compile(r"addons/news/produce\.py|" + RENDER_OR_CLEAN)
DIRECT = re.compile(RENDER_OR_CLEAN)
OTHERS_MAX_WAIT_S = 3 * 3600


def speed(proxy: str) -> float:
    """KB/s through a SOCKS proxy (0 when it does not answer)."""
    try:
        out = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{speed_download}", "--max-time", "15",
                              "-x", proxy.replace("socks5://", "socks5h://"),
                              "https://speed.cloudflare.com/__down?bytes=500000"],
                             capture_output=True, text=True, timeout=20).stdout
        return float(out or 0) / 1024
    except (subprocess.TimeoutExpired, ValueError):
        return 0.0


def flow_proxy() -> str:
    main = os.environ.get("FLOW_PROXY", "").split("#")[0].strip()
    if not main:
        return RESERVE_FLOW_PROXY
    a, b = speed(main), speed(RESERVE_FLOW_PROXY)
    pick = main if a >= 300 or a >= b else RESERVE_FLOW_PROXY
    print(f"flow egress: {main} {a:.0f} KB/s, reserve {b:.0f} KB/s -> {pick}", flush=True)
    return pick


def deliver_args(name: str) -> list[str]:
    """A film goes to the owner as a file (not recompressed) and to the news group(s) too — NEWS_TG_CHATS in the .env
    (owner 2026-10-30: «пусть бот в эту группу тоже отправляет … видео пусть отправляет файлом»). Over the bot's
    50 MB the file is a two-pass copy that fits (factory.tg.squeeze)."""
    chats = os.environ.get("NEWS_TG_CHATS", "").strip()
    return ["--file", "--name", name] + (["--also", chats] if chats else [])


def viral_line(story: dict) -> str:
    """«📱 TikTok: <account> · ролик N из 2 · выложить в HH:MM МСК» for a viral film (story.json "viral"), else ''."""
    v = story.get("viral") or {}
    if not v.get("account"):
        return ""
    try:
        conf = json.loads((REPO / "addons" / "news" / "tiktok_accounts.json").read_text(encoding="utf-8"))
        acc = next(x for x in conf["accounts"] if x["key"] == v["account"])
    except (OSError, ValueError, KeyError, StopIteration):
        return f"📱 TikTok: аккаунт {v['account']}"
    slot = int(v.get("slot") or 1)
    n = (slot - 1) // max(1, len(conf["accounts"])) + 1
    when = next((s.get("post_at") for s in conf.get("slots", []) if s.get("slot") == slot), "")
    if when:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone(timedelta(hours=3)))
        if now.strftime("%H:%M") > when:  # a late film (a slot run again): its window has passed
            return f"📱 TikTok: {acc['name']} · ролик {n} из 2 · окно {when} прошло — выложить сразу"
    return f"📱 TikTok: {acc['name']} · ролик {n} из 2" + (f" · выложить в {when} МСК" if when else "")


def heavy_jobs(rx: re.Pattern = HEAVY, skip: set[int] | None = None) -> list[str]:
    """Other films' heavy work running now (one heavy job at a time on this server, owner 2026-11-01)."""
    skip = (skip or set()) | {os.getpid(), os.getppid()}
    out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True).stdout
    jobs = []
    for line in out.splitlines():
        pid, _, args = line.strip().partition(" ")
        if pid.isdigit() and int(pid) not in skip and rx.search(args) and not args.lstrip().startswith(("ps ", "pgrep")):
            jobs.append(f"{pid} {args[:160]}")
    return jobs


def wait_for_others(rx: re.Pattern, max_s: float, say=print) -> bool:
    """Wait while another film renders or cleans; False when it is still at it after max_s."""
    t0, told = time.time(), False
    while True:
        jobs = heavy_jobs(rx)
        if not jobs:
            return True
        if not told:
            say("waiting for another film's heavy job: " + "; ".join(jobs[:3]))
            told = True
        if time.time() - t0 > max_s:
            return False
        time.sleep(30)


def run(cmd: list[str], env: dict | None = None, log: Path | None = None) -> subprocess.Popen:
    out = log.open("w") if log else None
    return subprocess.Popen(cmd, cwd=REPO, env={**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1"},
                            stdout=out or None, stderr=subprocess.STDOUT if out else None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--caption", type=Path, help="text file with the Telegram caption")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--kinescope", action="store_true")
    a = ap.parse_args()
    proj = (REPO / a.project).resolve() if not a.project.is_absolute() else a.project
    rel = proj.relative_to(REPO).as_posix()
    story = json.loads((proj / "story.json").read_text(encoding="utf-8"))
    logs = proj / "logs"
    logs.mkdir(exist_ok=True)

    def fail(reason: str) -> None:
        print(f"PRODUCE_RESULT: FAILED {rel} {reason}", flush=True)
        sys.exit(1)

    if not (proj / "assets" / "narration.mp3").is_file():
        fail("no voice yet: run build.py --voice-only and set the shots first")
    lock = BUILD_LOCK.open("w")
    print("heavy lock: waiting", flush=True)
    fcntl.flock(lock, fcntl.LOCK_EX)  # held through cleaning, the render, the shorts and the bot's squeeze
    print("heavy lock: taken", flush=True)
    # another produce.py waits on the lock; a render or a cleaning started by hand does not take it — wait for it too
    if not wait_for_others(DIRECT, OTHERS_MAX_WAIT_S, lambda m: print(m, flush=True)):
        fail("server busy: another film's render or cleaning ran for 3 h")
    jobs = {}
    if story.get("illustrations"):
        jobs["pictures"] = run([PY, "addons/news/gen_shots.py", f"{rel}/story.json", f"{rel}/illustrations"],
                               {"DISPLAY": os.environ.get("DISPLAY", ":99"), "FLOW_PROXY": flow_proxy()},
                               logs / "gen_shots.log")
    jobs["clean"] = run([PY, "addons/news/clean_footage.py", rel, "--run"], None, logs / "clean_footage.log")
    for name, p in jobs.items():
        code = p.wait()
        tail = (logs / f"{'gen_shots' if name == 'pictures' else 'clean_footage'}.log").read_text()[-400:]
        print(f"{name}: exit {code}\n{tail}", flush=True)
        if code == 3 and name == "pictures":
            fail("flow-quota")
        if code != 0:
            fail(f"{name} failed (see {rel}/logs)")
    missing = [i["id"] for i in story.get("illustrations", []) if not (proj / "illustrations" / f"{i['id']}.png").is_file()]
    if missing:
        fail(f"illustrations missing: {', '.join(missing)}")
    print("build: start", flush=True)
    b = run([PY, "addons/news/build.py", rel, "--retime"],
            {"https_proxy": os.environ.get("NEWS_HTTPS_PROXY", "http://127.0.0.1:10809"),
             "HYPERFRAMES_BIN": "/opt/node22/hyperframes.sh",
             "PATH": "/opt/node22/bin:" + os.environ.get("PATH", "/usr/bin:/bin")}, logs / "build.log")
    if b.wait() != 0:
        fail(f"build failed: {(logs / 'build.log').read_text()[-300:]}")
    shorts = []
    if story.get("stories") and (story.get("tiktok") or {}).get("split", True):
        print("shorts: start", flush=True)
        t = run([PY, "addons/news/build.py", rel, "--retime", "--split"],
                {"https_proxy": os.environ.get("NEWS_HTTPS_PROXY", "http://127.0.0.1:10809"),
                 "HYPERFRAMES_BIN": "/opt/node22/hyperframes.sh",
                 "PATH": "/opt/node22/bin:" + os.environ.get("PATH", "/usr/bin:/bin")}, logs / "shorts.log")
        if t.wait() != 0:  # the digest is out either way: a failed short is reported, not fatal
            print(f"shorts failed: {(logs / 'shorts.log').read_text()[-300:]}", flush=True)
        shorts = sorted((proj / "renders").glob("tiktok_*.mp4"), key=lambda f: int(f.stem.split("_")[1]))
    render = proj / "renders" / "final.mp4"
    print((logs / "build.log").read_text()[-400:], flush=True)
    if a.send:
        caption = a.caption.read_text(encoding="utf-8") if a.caption else story.get("headline", "")
        if viral_line(story):
            caption = viral_line(story) + "\n\n" + caption
        if subprocess.run([PY, "addons/mural/send_telegram.py", caption, str(render), *deliver_args(f"{proj.name}.mp4")], cwd=REPO,
                          env={**os.environ, "PYTHONPATH": str(REPO)}).returncode != 0:
            fail("telegram send failed")
        for f in shorts:
            n = int(f.stem.split("_")[1])
            head = story["stories"][n - 1].get("headline", "")
            subprocess.run([PY, "addons/mural/send_telegram.py", f"TikTok {n}/{len(story['stories'])}: {head}", str(f),
                            *deliver_args(f"{proj.name}-tiktok-{n}.mp4")],
                           cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO)})
    lock.close()  # cleaned, rendered, squeezed for the bot and sent: the next film may start (the upload is light)
    if a.kinescope:
        k = [PY, "addons/news/kinescope_upload.py", rel, "--notify"] + (["--replace"] if (proj / "kinescope.json").is_file() else [])
        if subprocess.run(k, cwd=REPO).returncode != 0:
            fail("kinescope upload failed")
    print(f"PRODUCE_RESULT: READY {rel}", flush=True)


if __name__ == "__main__":
    main()
