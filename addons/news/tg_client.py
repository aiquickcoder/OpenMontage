"""Telegram as an app (Telethon) for what the web preview does not give: a video too big for t.me/s — «Media is too
big» — comes with no file there (2026-10-30: IrinaVolk_MVD 4 videos of 12, СК Москвы 1 of 4). A user session reads
any public channel's post and downloads its video whole.

    # once, by the owner in their own terminal (QR in Telegram: Settings → Devices → Link Desktop Device; a cloud
    # password, if set, is typed by the owner):
    systemd-run --pty --quiet --uid=factory -p EnvironmentFile=/opt/openmontage/.env -E HOME=/home/factory \
        --working-directory=/opt/openmontage /opt/openmontage/.venv/bin/python addons/news/tg_client.py login
    # the harvesters, for a post whose video the preview lacks:
    .venv/bin/python addons/news/tg_client.py fetch <channel> <post id> <dst.mp4>

TG_API_ID / TG_API_HASH (my.telegram.org → API development tools) live only in /opt/openmontage/.env — the owner puts
them there; never printed. The session file is ~/.config/openmontage/telegram.session (0600) of the factory user: it is
an account login — a separate account is better. Telegram is reached through the factory's egress (HTTP proxy
127.0.0.1:10809; its data centres do not answer the server directly). fetch exits 3 when there is no session yet.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

SESSION = Path.home() / ".config" / "openmontage" / "telegram"
PROXY = os.environ.get("TG_CLIENT_PROXY", "http://127.0.0.1:10809")  # not TG_PROXY: the factory bot's


def client():
    from telethon import TelegramClient

    api_id, api_hash = os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        sys.exit("TG_API_ID / TG_API_HASH are not in the environment (run with EnvironmentFile=/opt/openmontage/.env)")
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    kind, rest = PROXY.split("://", 1)
    host, port = rest.rsplit(":", 1)
    return TelegramClient(str(SESSION), int(api_id), api_hash, proxy=(kind, host, int(port)))


async def login() -> None:
    import getpass

    import qrcode
    from telethon.errors import SessionPasswordNeededError

    c = client()
    await c.connect()
    if await c.is_user_authorized():
        print("already logged in as", (await c.get_me()).username or "(no username)")
        return
    from datetime import datetime, timezone

    qr = await c.qr_login()
    for _ in range(20):  # a code lives about 30 s: a fresh one each time, for about ten minutes
        q = qrcode.QRCode(border=1)
        q.add_data(qr.url)
        q.print_ascii(invert=True)
        left = max(5.0, (qr.expires - datetime.now(timezone.utc)).total_seconds())
        print(f"Telegram → Настройки → Устройства → Подключить устройство, отсканируйте код ({left:.0f} с)", flush=True)
        try:
            await qr.wait(left)
            break
        except asyncio.TimeoutError:
            await qr.recreate()
        except SessionPasswordNeededError:
            await c.sign_in(password=getpass.getpass("Облачный пароль Telegram: "))
            break
    os.chmod(f"{SESSION}.session", 0o600)
    print("logged in as", (await c.get_me()).username or "(no username)")
    await c.disconnect()


async def fetch(channel: str, post: int, dst: Path) -> int:
    c = client()
    await c.connect()
    try:
        if not await c.is_user_authorized():
            print("no Telegram session yet — the owner runs tg_client.py login once", file=sys.stderr)
            return 3
        msg = await c.get_messages(channel, ids=post)
        if not msg or not (msg.video or msg.document):
            print(f"{channel}/{post}: no video", file=sys.stderr)
            return 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        await c.download_media(msg, file=str(dst))
        return 0 if dst.is_file() and dst.stat().st_size > 1024 else 1
    finally:
        await c.disconnect()


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "login":
        asyncio.run(login())
    elif len(sys.argv) == 5 and sys.argv[1] == "fetch":
        sys.exit(asyncio.run(fetch(sys.argv[2], int(sys.argv[3]), Path(sys.argv[4]))))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
