"""Telegram as an app (Telethon) for what the web preview does not give: a video too big for t.me/s — «Media is too
big» — comes with no file there (2026-10-30: IrinaVolk_MVD 4 videos of 12, СК Москвы 1 of 4). A user session reads
any public channel's post and downloads its video whole.

    # once, by the owner in their own terminal (QR in Telegram: Settings → Devices → Link Desktop Device; a cloud
    # password, if set, is typed by the owner):
    systemd-run --pty --quiet --uid=factory -p EnvironmentFile=/opt/openmontage/.env -E HOME=/home/factory \
        --working-directory=/opt/openmontage /opt/openmontage/.venv/bin/python addons/news/tg_client.py login
    # the harvesters, for a post whose video the preview lacks (a photo post gives its photo):
    .venv/bin/python addons/news/tg_client.py fetch <channel> <post id> <dst.mp4|dst.jpg>
    # a big story from every source at once (Telegram's own search inside each channel, no joining needed):
    .venv/bin/python addons/news/tg_client.py search "Трибунал Польш" [--city russia | --channels a,b] [--hours 48] \
        [--json projects/<slug>/dossier.json]

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
        if not msg or not (msg.video or msg.document or msg.photo):
            print(f"{channel}/{post}: no video or photo", file=sys.stderr)
            return 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        await c.download_media(msg, file=str(dst))
        return 0 if dst.is_file() and dst.stat().st_size > 1024 else 1
    finally:
        await c.disconnect()


async def search(query: str, channels: list[str], hours: float = 48, per: int = 30) -> list[dict]:
    """Every post of these public channels that says the query, newer than `hours`, oldest first — the material of a
    big story (owner 2026-10-31: «через тг поискать … чтобы потом проще было реализовать такие новости на сервере»).
    A post of an album lists the album's media ("album": post ids), so its photos and videos can be fetched too."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    c = client()
    await c.connect()
    out: list[dict] = []
    try:
        if not await c.is_user_authorized():
            sys.exit("no Telegram session yet — the owner runs tg_client.py login once")
        for ch in channels:
            try:
                async for m in c.iter_messages(ch, search=query, limit=per):
                    if m.date < since:
                        break
                    album = [m.id]
                    if m.grouped_id:
                        near = await c.get_messages(ch, ids=list(range(m.id - 9, m.id + 10)))
                        album = [x.id for x in near if x and x.grouped_id == m.grouped_id]
                    out.append({"channel": ch, "post": m.id, "date": m.date.isoformat(), "views": m.views or 0,
                                "forwards": m.forwards or 0, "text": m.message or "",
                                "media": "video" if m.video else "photo" if m.photo else "", "album": album,
                                "link": f"https://t.me/{ch}/{m.id}", "ref": f"tg:{ch}/{m.id}"})
            except Exception as e:  # noqa: BLE001 — a renamed or private channel is skipped, the rest goes on
                print(f"{ch}: {e}", file=sys.stderr)
    finally:
        await c.disconnect()
    return sorted(out, key=lambda p: p["date"])


def city_channels(city: str | None) -> list[str]:
    import json

    cities = json.loads((Path(__file__).resolve().parent / "cities.json").read_text(encoding="utf-8"))
    seen: list[str] = []
    for k, v in cities.items():
        if isinstance(v, dict) and (city is None or k == city):
            for ch in v.get("channels") or []:
                ch = ch[0] if isinstance(ch, list) else ch
                if ch not in seen:
                    seen.append(ch)
    return seen


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "login":
        asyncio.run(login())
    elif len(sys.argv) == 5 and sys.argv[1] == "fetch":
        sys.exit(asyncio.run(fetch(sys.argv[2], int(sys.argv[3]), Path(sys.argv[4]))))
    elif len(sys.argv) >= 3 and sys.argv[1] == "search":
        import argparse
        import json

        ap = argparse.ArgumentParser(prog="tg_client.py search")
        ap.add_argument("query")
        ap.add_argument("--city")
        ap.add_argument("--channels")
        ap.add_argument("--hours", type=float, default=48)
        ap.add_argument("--json", type=Path)
        a = ap.parse_args(sys.argv[2:])
        chans = a.channels.split(",") if a.channels else city_channels(a.city)
        posts = asyncio.run(search(a.query, chans, a.hours))
        if a.json:
            a.json.parent.mkdir(parents=True, exist_ok=True)
            a.json.write_text(json.dumps(posts, ensure_ascii=False, indent=1), encoding="utf-8")
        for p in posts:
            text = " ".join(p["text"].split())[:150]
            print(f'{p["date"][5:16]}  {p["channel"]}/{p["post"]}  {p["views"]:>8}  {p["media"] or "-":5} '
                  f'{len(p["album"])}  {text}')
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
