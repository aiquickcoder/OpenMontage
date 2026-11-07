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
import hashlib
import json
import os
import re
import shutil
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


# a film on Kinescope keeps no video on the server (owner 2026-11-04: «если уже выгрузил в kinescope, то удаляй видео с
# диска. Почисти диск, ошибки получаем» — the disk filled up, the cleaner answered 507 and three films failed)
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".ts"}
MIN_FREE_GB = 6       # less than this after the sweep: stop with the reason, not a 507 halfway through the cleaning
RECENT_S = 2 * 3600   # a delivered film touched since then may be re-edited by an agent: its videos stay


def drop_videos(proj: Path) -> int:
    """Delete every video file of a project (not through symlinks); the bytes freed."""
    freed = 0
    for root, _dirs, files in os.walk(proj, followlinks=False):
        for name in files:
            f = Path(root) / name
            if f.suffix.lower() in VIDEO_EXT and f.is_file() and not f.is_symlink():
                freed += f.stat().st_size
                f.unlink()
    return freed


def sweep_delivered(skip: Path) -> int:
    """The videos of every film already on Kinescope (kinescope.json), except one touched in the last RECENT_S."""
    freed, now = 0, time.time()
    for k in (REPO / "projects").glob("*/kinescope.json"):
        p = k.parent
        if p == skip:
            continue
        latest = max((f.stat().st_mtime for f in (p / "story.json", p / "events.jsonl", k) if f.is_file()), default=0)
        if now - latest > RECENT_S:
            freed += drop_videos(p)
    return freed


def run(cmd: list[str], env: dict | None = None, log: Path | None = None) -> subprocess.Popen:
    out = log.open("w") if log else None
    return subprocess.Popen(cmd, cwd=REPO, env={**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1"},
                            stdout=out or None, stderr=subprocess.STDOUT if out else None)


def deliver_existing(proj: Path, story: dict, caption_path: Path | None, send: bool, kinescope: bool) -> None:
    """Resume an approved render without touching its script, voice or pictures.

    Save each Telegram acknowledgement immediately. An interrupted request is deliberately
    not retried: its outcome must be reconciled before risking a duplicate owner copy.
    """
    sys.path.insert(0, str(REPO))
    from factory import config
    from factory.tg import Telegram

    receipt_path = proj / "delivery.json"
    lock = (proj / ".delivery.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {"telegram": {}}

    def save() -> None:
        tmp = receipt_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(receipt_path)

    render = proj / "renders" / "final.mp4"
    if send:
        settings = config.load()
        chats = list(dict.fromkeys([settings.tg_chat] + [c.strip() for c in os.environ.get("NEWS_TG_CHATS", "").split(",") if c.strip()]))
        if not settings.tg_token or not settings.tg_chat:
            raise RuntimeError("Telegram is not configured")
        caption = caption_path.read_text(encoding="utf-8") if caption_path else story.get("headline", "")
        if viral_line(story):
            caption = viral_line(story) + "\n\n" + caption
        sha = hashlib.sha256(render.read_bytes()).hexdigest() if render.is_file() else receipt.get("render_sha256")
        if not sha:
            raise RuntimeError("no existing render or delivery receipt")
        if receipt.get("render_sha256") not in (None, sha):
            raise RuntimeError("render differs from delivery receipt; reconcile before sending")
        receipt["render_sha256"] = sha
        for chat in chats:
            old = receipt["telegram"].get(chat, {})
            if old.get("status") == "sent":
                print("telegram: acknowledged copy already delivered, skipping", flush=True)
                continue
            if old.get("status") == "sending":
                raise RuntimeError("Telegram request interrupted; reconcile its receipt before retrying")
            if not render.is_file():
                raise RuntimeError("no existing render for pending Telegram copy")
            receipt["telegram"][chat] = {"status": "sending", "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            save()
            result = Telegram(settings.tg_token, chat, settings.tg_proxy).send_document(render, caption, f"{proj.name}.mp4")
            if not result or not result.get("document"):
                raise RuntimeError("Telegram did not acknowledge a video document")
            receipt["telegram"][chat] = {"status": "sent", "message_id": result["message_id"],
                                         "date": result.get("date"), "file_id": result["document"].get("file_id")}
            save()
            print(f"telegram: video document acknowledged (message {result['message_id']})", flush=True)
    if kinescope:
        kpath = proj / "kinescope.json"
        if not kpath.is_file():
            if not render.is_file():
                raise RuntimeError("no existing render for Kinescope")
            if subprocess.run([PY, "addons/news/kinescope_upload.py", str(proj), "--notify"], cwd=REPO).returncode:
                raise RuntimeError("kinescope upload failed")
        out = json.loads(kpath.read_text())
        from addons.news.kinescope_upload import download_link
        status, link = download_link(os.environ["KINESCOPE_API_TOKEN"], out["id"])
        if status != "done" or not link:
            raise RuntimeError(f"Kinescope is not ready: {status}")
        receipt["kinescope"] = {"id": out["id"], "status": status, "play_link": out.get("play_link"), "download_link": link}
        save()
        print(f"disk: {drop_videos(proj) / 2**20:.0f} MB of this film's videos deleted — it is on Kinescope", flush=True)
    if (proj / "project.json").is_file() and send and kinescope:
        from lib.checkpoint import write_checkpoint

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        published = {"version": "1.0", "entries": [
            {"platform": "telegram", "status": "published", "timestamp": timestamp,
             "video_id": str(item["message_id"]), "visibility": "private"}
            for item in receipt["telegram"].values() if item.get("status") == "sent"
        ] + [{"platform": "kinescope", "status": "published", "timestamp": timestamp,
              "video_id": receipt["kinescope"]["id"], "url": receipt["kinescope"]["play_link"]}],
                     "metadata": {"master_output": "renders/final.mp4", "delivery_receipt": "delivery.json",
                                  "reused_approved_render": True, "local_video_removed_after_upload": True}}
        (proj / "artifacts").mkdir(exist_ok=True)
        (proj / "artifacts/publish_log.json").write_text(json.dumps(published, ensure_ascii=False, indent=2), encoding="utf-8")
        write_checkpoint(proj.parent, proj.name, "publish", "completed", {"publish_log": published},
                         human_approved=True, metadata={"authorization": "Explicit unattended full-run user authorization."})
    lock.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--caption", type=Path, help="text file with the Telegram caption")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--kinescope", action="store_true")
    ap.add_argument("--deliver-only", action="store_true", help="reuse the approved final.mp4; resume delivery using per-chat receipts")
    a = ap.parse_args()
    proj = (REPO / a.project).resolve() if not a.project.is_absolute() else a.project
    rel = proj.relative_to(REPO).as_posix()
    story = json.loads((proj / "story.json").read_text(encoding="utf-8"))
    logs = proj / "logs"
    logs.mkdir(exist_ok=True)

    def fail(reason: str) -> None:
        print(f"PRODUCE_RESULT: FAILED {rel} {reason}", flush=True)
        sys.exit(1)

    if a.deliver_only:
        try:
            deliver_existing(proj, story, a.caption, a.send, a.kinescope)
        except Exception as exc:
            fail(str(exc))
        print(f"PRODUCE_RESULT: READY {rel}", flush=True)
        return

    if not (proj / "assets" / "narration.mp3").is_file():
        fail("no voice yet: run build.py --voice-only and set the shots first")
    lock = BUILD_LOCK.open("w")
    print("heavy lock: waiting", flush=True)
    fcntl.flock(lock, fcntl.LOCK_EX)  # held through cleaning, the render, the shorts and the bot's squeeze
    print("heavy lock: taken", flush=True)
    # another produce.py waits on the lock; a render or a cleaning started by hand does not take it — wait for it too
    if not wait_for_others(DIRECT, OTHERS_MAX_WAIT_S, lambda m: print(m, flush=True)):
        fail("server busy: another film's render or cleaning ran for 3 h")
    if freed := sweep_delivered(proj):
        print(f"disk: {freed / 2**30:.1f} GB of videos of films already on Kinescope deleted", flush=True)
    if (free := shutil.disk_usage(REPO).free / 2**30) < MIN_FREE_GB:
        fail(f"disk full: {free:.1f} GB free, {MIN_FREE_GB} GB needed — nothing delivered left to delete")
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
    # every shot scored before the render (owner 2026-11-03: «ИИ оценивал кадры по бальной системе»; README «Оценка
    # кадров»): repeats and stills «around» the story stop the film; low scores too, for MAX_ROUNDS rounds
    # the judge mends instead of stopping (owner 2026-11-04: «оценщик не должен останавливать билд, он должен вносить
    # правки и билд должен продолжаться»): 4 — it replaced pieces in story.json, they are cleaned and the film goes on
    j = subprocess.run([PY, "addons/news/shot_judge.py", rel], cwd=REPO, capture_output=True, text=True)
    (logs / "shot_judge.log").write_text(j.stdout + j.stderr, encoding="utf-8")
    print(j.stdout.strip(), flush=True)
    if j.returncode == 4:
        c = run([PY, "addons/news/clean_footage.py", rel, "--run"], None, logs / "clean_footage.log")
        if c.wait() != 0:
            fail(f"clean of the judge's new pieces failed (see {rel}/logs)")
        j2 = subprocess.run([PY, "addons/news/shot_judge.py", rel, "--no-fix", "--no-ai"], cwd=REPO, capture_output=True,
                            text=True)
        (logs / "shot_judge_after_fix.log").write_text(j2.stdout + j2.stderr, encoding="utf-8")
        print(j2.stdout.strip(), flush=True)
    elif j.returncode != 0:  # the judge itself broke: the film goes on, the log says why
        print(f"shot judge error (not blocking): {(j.stderr or j.stdout)[-300:]}", flush=True)
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
        if (proj / "kinescope.json").is_file():
            print(f"disk: {drop_videos(proj) / 2**20:.0f} MB of this film's videos deleted — it is on Kinescope", flush=True)
    print(f"PRODUCE_RESULT: READY {rel}", flush=True)


if __name__ == "__main__":
    main()
