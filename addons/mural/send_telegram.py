"""Send mural results to the factory Telegram chat: an album of images and/or a video (with real dims).

Runs on the server with the factory env (token + proxy come from /opt/openmontage/.env, never from argv):

    systemd-run --wait --quiet --uid=factory -p EnvironmentFile=/opt/openmontage/.env \
      -p Environment=PYTHONPATH=/opt/openmontage -p WorkingDirectory=/opt/openmontage \
      /opt/openmontage/.venv/bin/python addons/mural/send_telegram.py "<caption>" file1.png file2.mp4 ... \
      [--file] [--name <file name>] [--also <chat>,<chat>]

--file sends a video as a file (not recompressed; the news films, owner 2026-10-30), --name names it, --also sends
the same to more chats (the news group, NEWS_TG_CHATS) after the owner's.
"""
import sys
from pathlib import Path

sys.path.insert(0, "/opt/openmontage")
from factory import config  # noqa: E402
from factory.tg import Telegram  # noqa: E402

IMAGES = {".png", ".jpg", ".jpeg", ".webp"}


def deliver(tg: Telegram, caption: str, files: list[Path], as_file: bool, name: str) -> None:
    if not files:  # text only (a narration script for the owner to record, a note)
        tg.send(caption)
        print("text sent", tg.chat, flush=True)
        return
    images = [p for p in files if p.suffix.lower() in IMAGES]
    for i in range(0, len(images), 10):
        chunk = images[i:i + 10]
        media = [{"type": "photo", "media": f"attach://f{j}", **({"caption": caption} if i == j == 0 else {})}
                 for j in range(len(chunk))]
        handles = {f"f{j}": (p.name, p.open("rb"), "image/jpeg" if p.suffix.lower() in {".jpg", ".jpeg"} else "image/png")
                   for j, p in enumerate(chunk)}
        tg.call("sendMediaGroup", chat_id=tg.chat, media=media, files=handles, http_timeout=300)
        print("album sent", [p.name for p in chunk], flush=True)
    for v in (p for p in files if p.suffix.lower() == ".mp4"):
        if as_file:
            tg.send_document(v, caption, name)
        else:
            tg.send_video(v, caption)
        print("video sent", v.name, tg.chat, flush=True)


def main() -> None:
    s = config.load()
    args = sys.argv[1:]
    opt = {}
    for flag in ("--name", "--also"):
        if flag in args:
            k = args.index(flag)
            opt[flag], args[k:k + 2] = args[k + 1], []
    as_file = "--file" in args
    args = [a for a in args if a != "--file"]
    caption, files = args[0], [Path(p) for p in args[1:] if Path(p).is_file()]
    chats = [s.tg_chat] + [c.strip() for c in opt.get("--also", "").split(",") if c.strip() and c.strip() != s.tg_chat]
    failed = 0
    for n, chat in enumerate(chats):
        tg = Telegram(s.tg_token, chat, s.tg_proxy)
        if not tg.enabled:
            sys.exit("Telegram is not configured (run with EnvironmentFile=/opt/openmontage/.env)")
        try:
            deliver(tg, caption, files, as_file, opt.get("--name", ""))
        except Exception as e:  # noqa: BLE001 — the owner's copy decides; another chat failing is reported
            if n == 0:
                raise
            failed += 1
            print(f"not sent to {chat}: {e}", file=sys.stderr, flush=True)
    if failed:
        print(f"{failed} extra chat(s) failed", flush=True)

if __name__ == "__main__":
    main()
