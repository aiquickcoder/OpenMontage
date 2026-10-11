"""Phone farm, server side: the Mac agent's WebSocket, a mirror of its state, a command queue.

The farm (adb + phones) runs on the owner's Mac; it connects OUT to the Studio at
/api/farm/ws and keeps it in sync:

    agent -> server: hello, heartbeat, snapshot (devices/accounts/device_status), jobs,
                     ack (command result), artifact (failure screenshots), frame (live screen),
                     live.ended {device, reason} (the agent stopped a stream on its own)
    server -> agent: sync {cursor}, command {command_id, kind, args}

Everything the browser sees comes from the mirror tables in studio.db, so the pages work
while the farm is offline; commands wait in farm_commands and go out on (re)connect
(sent-but-unacked ones are re-sent — the agent dedups by command_id).

Wiring (studio/app.py): `app.include_router(farm.router)` and `app.include_router(farm.ws_router)`.
The hub is created lazily per app (app.state.farm_hub); no background task is needed.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from studio import auth
from studio.config import StudioSettings

log = logging.getLogger("studio.farm")

MAX_FRAME = 4 * 1024 * 1024          # agent text frame
MAX_ARTIFACT = 2 * 1024 * 1024       # decoded
ONLINE_S = 90.0                      # heartbeat age that still counts as online
ACK_WAIT_S = 30.0                    # health/screenshot/config/retry wait for the ack
LIVE_FPS = 1.0
JOB_STATUSES = ("pending", "running", "done", "failed", "cancelled")
JOB_KINDS = ("publish", "warmup", "audience")
PLATFORMS = ("instagram", "tiktok", "youtube", "vk")
MODES = ("warmup", "audience")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ARTIFACT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.(png|jpe?g|txt|xml|json)$")
HOURS_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS farm_jobs (
    id INTEGER PRIMARY KEY, kind TEXT, account_id TEXT, device TEXT, release_id TEXT, status TEXT,
    payload TEXT, result TEXT, run_after REAL, created_at REAL, started_at REAL, finished_at REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS farm_jobs_updated ON farm_jobs (updated_at);
CREATE INDEX IF NOT EXISTS farm_jobs_kind ON farm_jobs (kind, release_id);
CREATE TABLE IF NOT EXISTS farm_commands (
    id TEXT PRIMARY KEY, kind TEXT, args TEXT, status TEXT, result TEXT, error TEXT,
    created_at REAL, sent_at REAL, finished_at REAL
);
CREATE INDEX IF NOT EXISTS farm_commands_status ON farm_commands (status, created_at);
CREATE TABLE IF NOT EXISTS farm_devices (name TEXT PRIMARY KEY, pos INTEGER, data TEXT);
CREATE TABLE IF NOT EXISTS farm_accounts (id TEXT PRIMARY KEY, pos INTEGER, data TEXT);
CREATE TABLE IF NOT EXISTS farm_device_status (name TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS farm_agent (key TEXT PRIMARY KEY, value TEXT);
"""


def _dumps(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _loads(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# -- store ----------------------------------------------------------------------
class FarmStore:
    """Mirror of the agent's state + the command queue (studio.db, WAL)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # agent key/values: hello, heartbeat, last_seen, snapshot_at
    def set_agent(self, **values: Any) -> None:
        with self._conn() as c:
            c.executemany("INSERT INTO farm_agent (key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                          [(k, _dumps(v)) for k, v in values.items()])

    def agent(self) -> dict[str, Any]:
        with self._conn() as c:
            return {r["key"]: _loads(r["value"]) for r in c.execute("SELECT key, value FROM farm_agent")}

    # snapshot
    def save_snapshot(self, devices: list[dict], accounts: list[dict], status: list[dict]) -> None:
        with self._conn() as c:
            c.execute("BEGIN")
            c.execute("DELETE FROM farm_devices")
            c.execute("DELETE FROM farm_accounts")
            c.execute("DELETE FROM farm_device_status")
            c.executemany("INSERT OR REPLACE INTO farm_devices (name, pos, data) VALUES (?, ?, ?)",
                          [(str(d["name"]), i, _dumps(d)) for i, d in enumerate(devices)])
            c.executemany("INSERT OR REPLACE INTO farm_accounts (id, pos, data) VALUES (?, ?, ?)",
                          [(str(a["id"]), i, _dumps(a)) for i, a in enumerate(accounts)])
            c.executemany("INSERT OR REPLACE INTO farm_device_status (name, data) VALUES (?, ?)",
                          [(str(s["name"]), _dumps(s)) for s in status])
            c.execute("COMMIT")
        self.set_agent(snapshot_at=time.time())

    def devices(self) -> list[dict]:
        with self._conn() as c:
            return [_loads(r["data"]) for r in c.execute("SELECT data FROM farm_devices ORDER BY pos")]

    def accounts(self) -> list[dict]:
        with self._conn() as c:
            return [_loads(r["data"]) for r in c.execute("SELECT data FROM farm_accounts ORDER BY pos")]

    def device_status(self) -> dict[str, dict]:
        with self._conn() as c:
            return {r["name"]: _loads(r["data"]) for r in c.execute("SELECT name, data FROM farm_device_status")}

    # jobs
    def upsert_jobs(self, rows: list[dict]) -> int:
        cols = ("kind", "account_id", "device", "release_id", "status", "payload", "result",
                "run_after", "created_at", "started_at", "finished_at", "updated_at")
        values = []
        for r in rows:
            values.append((int(r["id"]), str(r.get("kind") or ""), r.get("account_id"), r.get("device"),
                           r.get("release_id"), str(r.get("status") or ""), _dumps(r.get("payload") or {}),
                           _dumps(r.get("result")), _num(r.get("run_after")), _num(r.get("created_at")),
                           _num(r.get("started_at")), _num(r.get("finished_at")), _num(r.get("updated_at")) or 0.0))
        with self._conn() as c:
            c.executemany(
                f"INSERT INTO farm_jobs (id, {', '.join(cols)}) VALUES ({', '.join('?' * (len(cols) + 1))}) "
                f"ON CONFLICT(id) DO UPDATE SET {', '.join(f'{k} = excluded.{k}' for k in cols)}", values)
        return len(values)

    def cursor(self) -> float:
        with self._conn() as c:
            return float(c.execute("SELECT COALESCE(MAX(updated_at), 0) FROM farm_jobs").fetchone()[0])

    def jobs(self, where: str = "", params: tuple = (), order: str = "id DESC", limit: int = 2000) -> list[dict]:
        sql = f"SELECT * FROM farm_jobs {'WHERE ' + where if where else ''} ORDER BY {order} LIMIT ?"
        with self._conn() as c:
            return [self._job(r) for r in c.execute(sql, (*params, limit))]

    def job(self, job_id: int) -> Optional[dict]:
        rows = self.jobs("id = ?", (job_id,), limit=1)
        return rows[0] if rows else None

    @staticmethod
    def _job(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["payload"] = _loads(d["payload"]) or {}
        d["result"] = _loads(d["result"])
        return d

    # commands
    def add_command(self, command_id: str, kind: str, args: dict) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO farm_commands (id, kind, args, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
                      (command_id, kind, _dumps(args), time.time()))

    def mark_sent(self, command_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE farm_commands SET status = 'sent', sent_at = ? WHERE id = ? AND status IN ('queued', 'sent')",
                      (time.time(), command_id))

    def finish_command(self, command_id: str, ok: bool, result: Any, error: Optional[str]) -> bool:
        with self._conn() as c:
            cur = c.execute("UPDATE farm_commands SET status = ?, result = ?, error = ?, finished_at = ? WHERE id = ?",
                            ("done" if ok else "failed", _dumps(result), error, time.time(), command_id))
            return cur.rowcount > 0

    def command(self, command_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM farm_commands WHERE id = ?", (command_id,)).fetchone()
        return self._command(row) if row else None

    def commands(self, limit: int = 50, statuses: Optional[tuple[str, ...]] = None,
                 kind: Optional[str] = None, oldest_first: bool = False) -> list[dict]:
        where, params = [], []
        if statuses:
            where.append(f"status IN ({', '.join('?' * len(statuses))})")
            params += statuses
        if kind:
            where.append("kind = ?")
            params.append(kind)
        sql = (f"SELECT * FROM farm_commands {'WHERE ' + ' AND '.join(where) if where else ''} "
               f"ORDER BY created_at {'ASC' if oldest_first else 'DESC'}, rowid {'ASC' if oldest_first else 'DESC'} LIMIT ?")
        with self._conn() as c:
            return [self._command(r) for r in c.execute(sql, (*params, limit))]

    @staticmethod
    def _command(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["args"] = _loads(d["args"]) or {}
        d["result"] = _loads(d["result"])
        return d


# -- hub ------------------------------------------------------------------------
class FarmHub:
    """In-process state: the agent socket, ack waiters, live-frame subscribers."""

    def __init__(self, store: FarmStore, settings: StudioSettings):
        self.store = store
        self.settings = settings
        self.agent: Optional[WebSocket] = None
        self.ack_wait = ACK_WAIT_S
        self._waiters: dict[str, asyncio.Future] = {}
        self._viewers: dict[str, set[asyncio.Queue]] = {}
        self._send_lock: Optional[asyncio.Lock] = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def artifacts_dir(self) -> Path:
        return self.settings.factory.data_dir / "farm" / "artifacts"

    async def send(self, message: dict) -> bool:
        ws = self.agent
        if ws is None:
            return False
        self._send_lock = self._send_lock or asyncio.Lock()
        try:
            async with self._send_lock:
                await ws.send_text(json.dumps(message, ensure_ascii=False))
            return True
        except Exception:  # socket died under us; the command stays queued/sent and goes out on reconnect
            log.warning("farm agent send failed", exc_info=True)
            return False

    async def _deliver(self, cmd: dict) -> None:
        if await self.send({"type": "command", "command_id": cmd["id"], "kind": cmd["kind"], "args": cmd["args"]}):
            self.store.mark_sent(cmd["id"])

    async def dispatch(self, kind: str, args: dict, wait: Optional[float] = None) -> dict:
        """Persist a command, send it if the agent is here, optionally wait for its ack."""
        command_id = uuid.uuid4().hex
        self.store.add_command(command_id, kind, args)
        fut: Optional[asyncio.Future] = None
        if wait:
            fut = asyncio.get_running_loop().create_future()
            self._waiters[command_id] = fut
        try:
            if self.agent is not None:
                await self._deliver({"id": command_id, "kind": kind, "args": args})
            if fut is not None and self.agent is not None:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(fut), wait)
        finally:
            self._waiters.pop(command_id, None)
        return self.store.command(command_id) or {}

    async def flush(self) -> None:
        """On (re)connect: everything queued, plus sent-but-unacked, oldest first."""
        for cmd in self.store.commands(limit=1000, statuses=("queued", "sent"), oldest_first=True):
            await self._deliver(cmd)
        for device, viewers in self._viewers.items():
            if viewers:
                await self._live("live.start", device)

    def ack(self, command_id: str, ok: bool, result: Any, error: Optional[str]) -> None:
        if not self.store.finish_command(command_id, ok, result, error):
            return
        fut = self._waiters.get(command_id)
        if fut is not None and not fut.done():
            fut.set_result(ok)

    # live screen: start on the first viewer, stop when the last one leaves.
    # Not persisted — a stale live.start replayed on reconnect would stream to nobody.
    async def _live(self, kind: str, device: str) -> None:
        args = {"device": device, "fps": LIVE_FPS} if kind == "live.start" else {"device": device}
        await self.send({"type": "command", "command_id": uuid.uuid4().hex, "kind": kind, "args": args})

    async def watch(self, device: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2)
        viewers = self._viewers.setdefault(device, set())
        viewers.add(q)
        if len(viewers) == 1:
            await self._live("live.start", device)
        return q

    def unwatch(self, device: str, q: asyncio.Queue) -> None:
        """Sync on purpose: runs in the socket's `finally`, which may already be cancelled."""
        viewers = self._viewers.get(device, set())
        viewers.discard(q)
        if not viewers:
            self._viewers.pop(device, None)
            task = asyncio.get_running_loop().create_task(self._live("live.stop", device))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def viewers(self, device: str) -> int:
        return len(self._viewers.get(device, ()))

    def frame(self, device: str, ts: Any, jpeg_b64: str) -> None:
        msg = {"type": "frame", "ts": ts, "jpeg_b64": jpeg_b64}
        for q in self._viewers.get(device, ()):
            if q.full():  # slow browser: drop the oldest frame, keep the newest
                with suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(msg)

    async def live_ended(self, device: str, reason: str) -> None:
        """The agent stopped a stream by itself (time budget or capture errors) while browsers may watch."""
        if not self._viewers.get(device):
            return
        if reason == "timeout":  # re-arm: the agent caps a stream at 15 min, the viewer is still here
            await self._live("live.start", device)
            return
        msg = {"type": "live.ended", "reason": reason}
        for q in self._viewers.get(device, ()):
            if q.full():
                with suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(msg)

    def save_artifact(self, msg: dict) -> Optional[Path]:
        job_id, name = msg.get("job_id"), str(msg.get("name") or "")
        if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id < 0 or not ARTIFACT_RE.match(name):
            log.warning("farm artifact rejected: bad job_id/name %r/%r", job_id, name)
            return None
        data_b64 = str(msg.get("data_b64") or "")
        if len(data_b64) > MAX_ARTIFACT * 4 // 3 + 8:
            log.warning("farm artifact rejected: too large (%s)", name)
            return None
        try:
            data = base64.b64decode(data_b64, validate=True)
        except (binascii.Error, ValueError):
            log.warning("farm artifact rejected: bad base64 (%s)", name)
            return None
        if len(data) > MAX_ARTIFACT:
            return None
        target = self.artifacts_dir / str(job_id) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)
        return target


_hub_lock = threading.Lock()


def get_hub(app: FastAPI) -> FarmHub:
    hub = getattr(app.state, "farm_hub", None)
    if hub is None:
        with _hub_lock:
            hub = getattr(app.state, "farm_hub", None)
            if hub is None:
                settings: StudioSettings = app.state.settings
                hub = app.state.farm_hub = FarmHub(FarmStore(settings.db_path), settings)
    return hub


def _hub(request: Request) -> FarmHub:
    return get_hub(request.app)


# -- agent socket ---------------------------------------------------------------
ws_router = APIRouter(prefix="/api")  # no require_user here: each socket authenticates itself


def _agent_token_ok(settings: StudioSettings, header: str) -> bool:
    expected = settings.farm_agent_token
    scheme, _, token = (header or "").partition(" ")
    if not expected or scheme.lower() != "bearer" or not token:
        return False
    return hmac.compare_digest(token.strip().encode(), expected.encode())


@ws_router.websocket("/farm/ws")
async def agent_socket(ws: WebSocket) -> None:
    hub = get_hub(ws.app)
    if not _agent_token_ok(hub.settings, ws.headers.get("authorization", "")):
        await ws.accept()
        await ws.close(code=4401)
        return
    await ws.accept()
    old, hub.agent = hub.agent, ws
    if old is not None:  # one agent at a time: the newest wins
        with suppress(Exception):
            await old.close(code=4000)
    hub.store.set_agent(connected_at=time.time(), last_seen=time.time())
    try:
        while hub.agent is ws:
            text = await ws.receive_text()
            if len(text) > MAX_FRAME:
                await ws.close(code=1009)
                break
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if isinstance(msg, dict):
                await _on_agent_message(hub, msg)
    except WebSocketDisconnect:
        pass
    finally:
        if hub.agent is ws:
            hub.agent = None
        hub.store.set_agent(last_seen=time.time(), disconnected_at=time.time())


async def _on_agent_message(hub: FarmHub, msg: dict) -> None:
    kind = msg.get("type")
    store = hub.store
    now = time.time()
    if kind != "frame":
        store.set_agent(last_seen=now)
    if kind == "hello":
        store.set_agent(hello={"agent_version": str(msg.get("agent_version") or ""),
                               "host": str(msg.get("host") or ""), "at": now})
        await hub.send({"type": "sync", "cursor": store.cursor()})
        await hub.flush()
    elif kind == "heartbeat":
        store.set_agent(heartbeat={"received_at": now, "ts": _num(msg.get("ts")),
                                   "scheduler_alive": bool(msg.get("scheduler_alive")),
                                   "scheduler_heartbeat_at": _num(msg.get("scheduler_heartbeat_at"))})
    elif kind == "snapshot":
        devices = [d for d in msg.get("devices") or [] if isinstance(d, dict) and d.get("name")]
        accounts = [a for a in msg.get("accounts") or [] if isinstance(a, dict) and a.get("id")]
        status = [s for s in msg.get("device_status") or [] if isinstance(s, dict) and s.get("name")]
        store.save_snapshot(devices, accounts, status)
    elif kind == "jobs":
        rows = [r for r in (msg.get("rows") or [])[:500]
                if isinstance(r, dict) and isinstance(r.get("id"), int)]
        store.upsert_jobs(rows)
    elif kind == "ack":
        if msg.get("command_id"):
            result = msg.get("result")
            hub.ack(str(msg["command_id"]), bool(msg.get("ok")), result if isinstance(result, dict) else None,
                    str(msg["error"]) if msg.get("error") else None)
    elif kind == "artifact":
        hub.save_artifact(msg)
    elif kind == "frame":
        device = str(msg.get("device") or "")
        if device and isinstance(msg.get("jpeg_b64"), str):
            hub.frame(device, msg.get("ts"), msg["jpeg_b64"])
    elif kind == "live.ended":
        device = str(msg.get("device") or "")
        if device:
            await hub.live_ended(device, str(msg.get("reason") or ""))


# -- browser live socket ----------------------------------------------------------
def _same_origin(ws: WebSocket) -> bool:
    origin, host = ws.headers.get("origin", ""), ws.headers.get("host", "")
    if not origin or not host:
        return False
    parsed = urlparse(origin)
    return parsed.scheme in ("http", "https") and parsed.netloc.lower() == host.lower()


@ws_router.websocket("/devices/{name}/live")
async def live_socket(ws: WebSocket, name: str) -> None:
    hub = get_hub(ws.app)
    settings = hub.settings
    user = (auth.read_session(settings.secret, ws.cookies.get(auth.COOKIE, ""))
            if auth.secret_ok(settings.secret) else None)
    await ws.accept()
    if not user or user != settings.admin_user:
        await ws.close(code=4401)
        return
    if not _same_origin(ws):
        await ws.close(code=4403)
        return
    if not NAME_RE.match(name) or name not in {d.get("name") for d in _devices(hub)}:
        await ws.close(code=4404)
        return
    q = await hub.watch(name)
    receiver = asyncio.create_task(ws.receive())
    try:
        while True:
            getter = asyncio.create_task(q.get())
            done, _ = await asyncio.wait({receiver, getter}, return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                getter.cancel()
                if receiver.result().get("type") == "websocket.disconnect":
                    break
                receiver = asyncio.create_task(ws.receive())  # browsers may ping; ignore the content
                continue
            await ws.send_text(json.dumps(getter.result()))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        receiver.cancel()
        hub.unwatch(name, q)


# -- config helpers ---------------------------------------------------------------
def _pending_config(hub: FarmHub, key: str) -> Optional[list[dict]]:
    """The newest config.update for `key` the agent hasn't reflected in a snapshot yet.

    Edits made while the farm is offline (or before its next snapshot) build on each other
    instead of each starting from the stale snapshot and silently undoing the previous one.
    """
    snapshot_at = hub.store.agent().get("snapshot_at") or 0
    for cmd in hub.store.commands(limit=20, statuses=("queued", "sent", "done"), kind="config.update"):
        if cmd["created_at"] <= snapshot_at:
            break
        if key in cmd["args"]:
            return cmd["args"][key]
    return None


def _devices(hub: FarmHub) -> list[dict]:
    pending = _pending_config(hub, "devices")
    return pending if pending is not None else hub.store.devices()


def _accounts(hub: FarmHub) -> list[dict]:
    pending = _pending_config(hub, "accounts")
    return pending if pending is not None else hub.store.accounts()


def _device_or_404(hub: FarmHub, name: str) -> dict:
    for d in _devices(hub):
        if d.get("name") == name:
            return d
    raise HTTPException(status_code=404, detail=f"нет устройства {name}")


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=422, detail=msg)


def _sessions(value: Any) -> Any:
    if isinstance(value, bool):
        raise _bad("сеансов в день: число 0–20 или диапазон «2-3»")
    if isinstance(value, int):
        lo = hi = value
    else:
        text = str(value or "").strip()
        m = re.fullmatch(r"(\d{1,2})(?:\s*-\s*(\d{1,2}))?", text)
        if not m:
            raise _bad("сеансов в день: число 0–20 или диапазон «2-3»")
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    if not (0 <= lo <= hi <= 20):
        raise _bad("сеансов в день: от 0 до 20, начало диапазона не больше конца")
    return lo if lo == hi else f"{lo}-{hi}"


def _hours(value: Any) -> list[str]:
    items = [value] if isinstance(value, str) else list(value or [])
    items = [str(x).replace(" ", "") for x in items if str(x).strip()]
    if not items or len(items) > 6:
        raise _bad("рабочие часы: от 1 до 6 интервалов «ЧЧ:ММ-ЧЧ:ММ»")
    for item in items:
        if not HOURS_RE.match(item):
            raise _bad(f"рабочие часы: «{item}» — нужен формат ЧЧ:ММ-ЧЧ:ММ, например 10:00-13:00")
        start, end = item.split("-")
        if start >= end:
            raise _bad(f"рабочие часы: в «{item}» начало должно быть раньше конца")
    return items


def _words(value: Any, field: str) -> list[str]:
    items = re.split(r"[\s,]+", value) if isinstance(value, str) else list(value or [])
    items = [str(x).strip().lstrip("#@") for x in items if str(x).strip()]
    if len(items) > 50 or any(len(x) > 64 for x in items):
        raise _bad(f"{field}: не больше 50 слов по 64 символа")
    return items


def _clean_account(raw: dict, devices: list[dict]) -> dict:
    """Validate one account into the accounts.yaml shape; unknown keys pass through untouched."""
    acc = dict(raw)
    acc["id"] = str(acc.get("id") or "").strip()
    if not NAME_RE.match(acc["id"]):
        raise _bad("id: латиница, цифры, «_», «-», «.» (до 64 символов), например ig_fox")
    if acc.get("platform") not in PLATFORMS:
        raise _bad(f"платформа: одна из {', '.join(PLATFORMS)}")
    acc["username"] = str(acc.get("username") or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", acc["username"]):
        raise _bad("логин: латиница, цифры, «.», «_», «-», без @ и пробелов")
    if acc.get("device") not in {d.get("name") for d in devices}:
        raise _bad(f"устройство «{acc.get('device') or ''}» не найдено на ферме")
    started = acc.get("started_at") or date.today().isoformat()
    try:
        acc["started_at"] = date.fromisoformat(str(started)[:10]).isoformat()
    except ValueError:
        raise _bad("дата старта: ГГГГ-ММ-ДД")
    acc["mode"] = acc.get("mode") or "warmup"
    if acc["mode"] not in MODES:
        raise _bad("режим: warmup (прогрев) или audience (аудитория)")
    acc["sessions_per_day"] = _sessions(acc.get("sessions_per_day", "2-3"))
    acc["working_hours"] = _hours(acc.get("working_hours") or ["10:00-13:00", "18:00-22:30"])
    acc["allow_follows"] = bool(acc.get("allow_follows", False))
    acc["hashtags"] = _words(acc.get("hashtags"), "хэштеги")
    acc["bloggers"] = _words(acc.get("bloggers"), "блогеры")
    if acc.get("package") and not PACKAGE_RE.match(str(acc["package"])):
        raise _bad("пакет приложения: вида com.instagram.android")
    if not acc.get("package"):
        acc.pop("package", None)
    return acc


def _check_accounts(accounts: list[dict]) -> None:
    ids, slots = set(), set()
    for a in accounts:
        if a["id"] in ids:
            raise HTTPException(status_code=409, detail=f"аккаунт {a['id']} уже есть")
        ids.add(a["id"])
        slot = (a["device"], a["platform"])
        if slot in slots:
            raise HTTPException(status_code=409,
                                detail=f"на {a['device']} уже есть аккаунт {a['platform']} — один на платформу")
        slots.add(slot)


async def _config_update(hub: FarmHub, **lists: list[dict]) -> JSONResponse:
    cmd = await hub.dispatch("config.update", lists, wait=hub.ack_wait)
    return _command_response(cmd)


def _command_response(cmd: dict) -> JSONResponse:
    """200 with the result once acked (failed acks become 409), 202 while still queued/sent."""
    body = {k: cmd.get(k) for k in ("id", "kind", "status", "result", "error")}
    body["command_id"] = body.pop("id")
    if cmd.get("status") == "failed":
        body["detail"] = f"ферма отклонила команду: {cmd.get('error') or 'без описания'}"
        return JSONResponse(body, status_code=409)
    return JSONResponse(body, status_code=200 if cmd.get("status") == "done" else 202)


# -- browser API ------------------------------------------------------------------
router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])


class AccountIn(BaseModel):
    id: Optional[str] = Field(default=None, max_length=64)
    platform: Optional[str] = None
    username: Optional[str] = Field(default=None, max_length=100)
    device: Optional[str] = Field(default=None, max_length=64)
    started_at: Optional[str] = Field(default=None, max_length=40)
    mode: Optional[str] = None
    sessions_per_day: Optional[int | str] = None
    working_hours: Optional[list[str] | str] = None
    allow_follows: Optional[bool] = None
    hashtags: Optional[list[str] | str] = None
    bloggers: Optional[list[str] | str] = None
    package: Optional[str] = Field(default=None, max_length=120)


class DeviceIn(BaseModel):
    require_vpn: Optional[bool] = None
    pinned_apps: Optional[dict[str, str]] = None


class PublishIn(BaseModel):
    accounts: list[str] = Field(min_length=1, max_length=50)
    start_at: Optional[datetime] = None
    spread_minutes: int = Field(default=0, ge=0, le=24 * 60)


def _status(hub: FarmHub) -> dict:
    agent = hub.store.agent()
    beat = agent.get("heartbeat") or {}
    hello = agent.get("hello") or {}
    now = time.time()
    received = beat.get("received_at")
    return {
        "online": bool(received and now - received < ONLINE_S),
        "connected": hub.agent is not None,
        "last_seen": agent.get("last_seen"),
        "last_heartbeat": received,
        "agent_version": hello.get("agent_version"),
        "host": hello.get("host"),
        "scheduler_alive": bool(beat.get("scheduler_alive")) if beat else None,
        "scheduler_heartbeat_at": beat.get("scheduler_heartbeat_at"),
        "snapshot_at": agent.get("snapshot_at"),
        "queued_commands": len(hub.store.commands(limit=1000, statuses=("queued", "sent"))),
        "server_time": now,
    }


@router.get("/farm/status")
def farm_status(request: Request) -> dict:
    return _status(_hub(request))


@router.get("/devices")
def list_devices(request: Request) -> list[dict]:
    hub = _hub(request)
    status = hub.store.device_status()
    accounts = _accounts(hub)
    active = hub.store.jobs("status IN ('pending', 'running')", order="run_after ASC", limit=5000)
    rows = []
    for d in _devices(hub):
        name = d.get("name")
        mine = [j for j in active if j["device"] == name]
        current = next((j for j in mine if j["status"] == "running"), None)
        upcoming = next((j for j in mine if j["status"] == "pending"), None)
        rows.append({**d, "status": status.get(name), "accounts": [a for a in accounts if a.get("device") == name],
                     "current_job": current, "next_job": upcoming,
                     "pending_jobs": sum(1 for j in mine if j["status"] == "pending"),
                     "live_viewers": hub.viewers(name)})
    return rows


@router.patch("/devices/{name}")
async def edit_device(name: str, body: DeviceIn, request: Request) -> JSONResponse:
    hub = _hub(request)
    _device_or_404(hub, name)
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise _bad("нечего менять")
    if "pinned_apps" in patch:
        pinned = {k.strip(): v.strip() for k, v in patch["pinned_apps"].items()}
        for pkg, version in pinned.items():
            if not PACKAGE_RE.match(pkg) or len(pkg) > 120:
                raise _bad(f"пакет «{pkg}»: вида com.instagram.android")
            if len(version) > 40 or not re.fullmatch(r"[\w.\-+ ]*", version):
                raise _bad(f"версия для {pkg}: до 40 символов, цифры и точки")
        patch["pinned_apps"] = pinned
    devices = [{**d, **patch} if d.get("name") == name else d for d in _devices(hub)]
    return await _config_update(hub, devices=devices)


async def _device_command(request: Request, name: str, kind: str, wait: bool) -> JSONResponse:
    hub = _hub(request)
    _device_or_404(hub, name)
    cmd = await hub.dispatch(kind, {"device": name}, wait=hub.ack_wait if wait else None)
    return _command_response(cmd)


@router.post("/devices/{name}/pause")
async def pause_device(name: str, request: Request) -> JSONResponse:
    return await _device_command(request, name, "device.pause", wait=False)


@router.post("/devices/{name}/resume")
async def resume_device(name: str, request: Request) -> JSONResponse:
    return await _device_command(request, name, "device.resume", wait=False)


@router.post("/devices/{name}/health")
async def device_health(name: str, request: Request) -> JSONResponse:
    return await _device_command(request, name, "device.health", wait=True)


@router.post("/devices/{name}/screenshot")
async def device_screenshot(name: str, request: Request) -> JSONResponse:
    return await _device_command(request, name, "device.screenshot", wait=True)


@router.get("/farm/commands")
def list_commands(request: Request, limit: int = 50) -> list[dict]:
    rows = _hub(request).store.commands(limit=max(1, min(limit, 500)))
    for row in rows:  # screenshots are big; the single-command endpoint has them
        if isinstance(row.get("result"), dict) and "jpeg_b64" in row["result"]:
            row["result"] = {"jpeg_b64": "…"}
    return rows


@router.get("/farm/commands/{command_id}")
def get_command(command_id: str, request: Request) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", command_id):
        raise HTTPException(status_code=400, detail="invalid command id")
    cmd = _hub(request).store.command(command_id)
    if cmd is None:
        raise HTTPException(status_code=404, detail="unknown command")
    return cmd


@router.get("/accounts")
def list_accounts(request: Request) -> dict:
    hub = _hub(request)
    pending = _pending_config(hub, "accounts") is not None
    return {"accounts": _accounts(hub), "devices": [d.get("name") for d in _devices(hub)],
            "platforms": list(PLATFORMS), "pending": pending}


@router.post("/accounts")
async def create_account(body: AccountIn, request: Request) -> JSONResponse:
    hub = _hub(request)
    devices = _devices(hub)
    new = _clean_account(body.model_dump(exclude_none=True), devices)
    accounts = [*_accounts(hub), new]
    _check_accounts(accounts)
    return await _config_update(hub, accounts=accounts)


@router.patch("/accounts/{account_id}")
async def edit_account(account_id: str, body: AccountIn, request: Request) -> JSONResponse:
    hub = _hub(request)
    current = _accounts(hub)
    if not any(a.get("id") == account_id for a in current):
        raise HTTPException(status_code=404, detail=f"нет аккаунта {account_id}")
    patch = body.model_dump(exclude_none=True)
    if patch.get("id", account_id) != account_id:
        raise _bad("id аккаунта менять нельзя — удалите и создайте новый")
    devices = _devices(hub)
    accounts = [_clean_account({**a, **patch}, devices) if a.get("id") == account_id else a for a in current]
    _check_accounts(accounts)
    return await _config_update(hub, accounts=accounts)


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, request: Request) -> JSONResponse:
    hub = _hub(request)
    current = _accounts(hub)
    if not any(a.get("id") == account_id for a in current):
        raise HTTPException(status_code=404, detail=f"нет аккаунта {account_id}")
    return await _config_update(hub, accounts=[a for a in current if a.get("id") != account_id])


def _artifacts(hub: FarmHub, job_id: int) -> list[str]:
    d = hub.artifacts_dir / str(job_id)
    if not d.is_dir():
        return []
    return [f"/api/farm/artifacts/{job_id}/{p.name}" for p in sorted(d.iterdir())
            if p.is_file() and ARTIFACT_RE.match(p.name)]


def _release_of(job: dict) -> str:
    payload = job.get("payload") or {}
    return job.get("release_id") or payload.get("release_id") or payload.get("project") or ""


@router.get("/publications")
def publications(request: Request, release_id: Optional[str] = None, account: Optional[str] = None,
                 status: Optional[str] = None) -> dict:
    """Publish jobs as a grid: one row per release, one cell per account (latest attempt wins)."""
    hub = _hub(request)
    where, params = ["kind = 'publish'"], []
    if account:
        where.append("account_id = ?")
        params.append(account)
    if status:
        states = tuple(s for s in status.split(",") if s in JOB_STATUSES)
        if states:
            where.append(f"status IN ({', '.join('?' * len(states))})")
            params += states
    jobs = hub.store.jobs(" AND ".join(where), tuple(params), order="id ASC", limit=5000)
    if release_id:
        jobs = [j for j in jobs if _release_of(j) == release_id]
    platform = {a.get("id"): a.get("platform") for a in _accounts(hub)}
    grid: dict[str, dict] = {}
    for j in jobs:
        result = j.get("result") or {}
        rel = _release_of(j)
        row = grid.setdefault(rel, {"release_id": rel or None, "cells": {}, "updated_at": 0.0})
        prev = row["cells"].get(j["account_id"])
        row["cells"][j["account_id"]] = {
            "job_id": j["id"], "account_id": j["account_id"], "platform": platform.get(j["account_id"]),
            "device": j["device"], "status": j["status"],
            "confirmed": bool(result.get("confirmed")) if isinstance(result, dict) else False,
            "url": auth.http_url(result.get("url")) if isinstance(result, dict) else None,
            "error": result.get("error") if isinstance(result, dict) else None,
            "run_after": j["run_after"], "created_at": j["created_at"], "started_at": j["started_at"],
            "finished_at": j["finished_at"], "updated_at": j["updated_at"],
            "attempts": (prev["attempts"] + 1) if prev else 1, "artifacts": _artifacts(hub, j["id"]),
        }
        row["updated_at"] = max(row["updated_at"], j["updated_at"] or 0.0)
    rows = sorted(grid.values(), key=lambda r: r["updated_at"], reverse=True)
    used = sorted({a for r in rows for a in r["cells"]})
    return {"rows": rows, "accounts": [{"id": a, "platform": platform.get(a)} for a in used],
            "all_accounts": [{"id": a.get("id"), "platform": a.get("platform")} for a in _accounts(hub)]}


def _publish_job(hub: FarmHub, job_id: int) -> dict:
    job = hub.store.job(job_id)
    if job is None or job["kind"] != "publish":
        raise HTTPException(status_code=404, detail="нет такой публикации")
    return job


@router.post("/publications/{job_id}/retry")
async def retry_publication(job_id: int, request: Request) -> JSONResponse:
    hub = _hub(request)
    job = _publish_job(hub, job_id)
    if job["status"] not in ("failed", "cancelled"):
        raise HTTPException(status_code=409, detail="повторить можно только упавшую или отменённую публикацию")
    return _command_response(await hub.dispatch("job.retry", {"job_id": job_id}, wait=hub.ack_wait))


@router.post("/publications/{job_id}/cancel")
async def cancel_publication(job_id: int, request: Request) -> JSONResponse:
    hub = _hub(request)
    job = _publish_job(hub, job_id)
    if job["status"] != "pending":
        raise HTTPException(status_code=409, detail="отменить можно только публикацию в очереди")
    return _command_response(await hub.dispatch("job.cancel", {"job_id": job_id}, wait=hub.ack_wait))


@router.get("/farm/artifacts/{job_id}/{name}")
def artifact(job_id: int, name: str, request: Request) -> FileResponse:
    if not ARTIFACT_RE.match(name):
        raise HTTPException(status_code=400, detail="invalid artifact name")
    target = _hub(request).artifacts_dir / str(job_id) / name
    if not target.is_file():
        raise HTTPException(status_code=404, detail="no artifact")
    return FileResponse(target)


@router.post("/videos/{brief_id}/publish")
async def publish_video(brief_id: str, body: PublishIn, request: Request) -> JSONResponse:
    from factory.db import BriefStore
    from studio import projects

    hub = _hub(request)
    brief_id = projects.safe_id(brief_id)
    store = getattr(request.app.state, "store", None) or BriefStore(hub.settings.factory.db_path)
    brief = store.get(brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="нет такого ролика")
    if brief["state"] != "approved":
        raise HTTPException(status_code=409, detail="публиковать можно только одобренный ролик")
    known = {a.get("id") for a in _accounts(hub)}
    unknown = [a for a in body.accounts if a not in known]
    if unknown:
        raise _bad(f"нет аккаунтов: {', '.join(unknown)}")
    if len(set(body.accounts)) != len(body.accounts):
        raise _bad("аккаунты повторяются")
    args = {"release_id": brief_id, "accounts": body.accounts,
            "start_at": body.start_at.isoformat() if body.start_at else None,
            "spread_minutes": body.spread_minutes}
    # no wait: the agent may first pull the release, which takes a while
    return _command_response(await hub.dispatch("publish.enqueue", args))
