"""Send mural results to the factory Telegram chat: an album of images and/or a video (with real dims).

Runs on the server with the factory env (token + proxy come from /opt/openmontage/.env, never from argv):

    systemd-run --wait --quiet --uid=factory -p EnvironmentFile=/opt/openmontage/.env \
      -p Environment=PYTHONPATH=/opt/openmontage -p WorkingDirectory=/opt/openmontage \
      /opt/openmontage/.venv/bin/python addons/mural/send_telegram.py "<caption>" file1.png file2.mp4 ...
"""
import sys
from pathlib import Path

sys.path.insert(0, "/opt/openmontage")
from factory import config  # noqa: E402
from factory.tg import Telegram  # noqa: E402

IMAGES = {".png", ".jpg", ".jpeg", ".webp"}


def main() -> None:
    s = config.load()
    tg = Telegram(s.tg_token, s.tg_chat, s.tg_proxy)
    if not tg.enabled:
        sys.exit("Telegram is not configured (run with EnvironmentFile=/opt/openmontage/.env)")
    caption, files = sys.argv[1], [Path(p) for p in sys.argv[2:] if Path(p).is_file()]
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
        tg.send_video(v, caption)
        print("video sent", v.name, flush=True)


if __name__ == "__main__":
    main()
