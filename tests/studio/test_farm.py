"""Farm server side: agent socket protocol, mirror tables, command queue, browser API, live relay."""

import base64
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from factory.db import BriefStore
from studio import farm
from studio.app import create_app
from tests.studio.test_studio_api import H, PASSWORD, _settings, _staged_release

AGENT = {"Authorization": "Bearer t"}
ORIGIN = {"Origin": "http://testserver"}

DEVICES = [{"name": "phone1", "serial": "R58", "require_vpn": True, "pinned_apps": {}},
           {"name": "phone2", "serial": "R59", "require_vpn": False, "pinned_apps": {}}]
ACCOUNTS = [{"id": "ig_fox", "platform": "instagram", "username": "fox", "device": "phone1",
             "started_at": "2026-10-09", "mode": "warmup", "sessions_per_day": "2-3",
             "working_hours": ["10:00-13:00"], "hashtags": ["мультики"], "custom": 1}]
STATUS = [{"name": "phone1", "serial": "R58", "adb_state": "device", "ok": True, "problems": [],
           "info": {"battery": 80}, "checked_at": 1.0, "paused": False, "busy": False}]


def _job(i, **kw):
    row = {"id": i, "kind": "publish", "account_id": "ig_fox", "device": "phone1", "release_id": "rel-1",
           "status": "pending", "payload": {}, "result": None, "run_after": 1.0, "created_at": 1.0,
           "started_at": None, "finished_at": None, "updated_at": 100.0 + i}
    row.update(kw)
    return row


@pytest.fixture
def env(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    app.include_router(farm.router)
    app.include_router(farm.ws_router)
    with TestClient(app) as client:  # one portal: agent socket, browser socket and HTTP share a loop
        farm.get_hub(app).ack_wait = 3.0
        yield settings, app, client


def _login(client):
    assert client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 200


def _until(fn, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def _hello(ws, snapshot=True):
    ws.send_json({"type": "hello", "agent_version": "1.0", "host": "mac"})
    sync = ws.receive_json()
    assert sync["type"] == "sync"
    if snapshot:
        ws.send_json({"type": "snapshot", "devices": DEVICES, "accounts": ACCOUNTS, "device_status": STATUS})
        ws.send_json({"type": "heartbeat", "ts": time.time(), "scheduler_alive": True, "scheduler_heartbeat_at": 1.0})
    return sync


def _snapshot_seen(client):
    return _until(lambda: client.get("/api/devices").json())


def test_agent_token_rejected(env):
    _, app, client = env
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic t"}):
        with client.websocket_connect("/api/farm/ws", headers=headers) as ws:
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4401
    # empty server token rejects everyone
    object.__setattr__(farm.get_hub(app).settings, "farm_agent_token", "")
    with client.websocket_connect("/api/farm/ws", headers={"Authorization": "Bearer "}) as ws:
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_hello_sync_snapshot_and_jobs(env):
    _, app, client = env
    _login(client)
    assert client.get("/api/farm/status").json()["online"] is False
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as ws:
        assert _hello(ws)["cursor"] == 0
        ws.send_json({"type": "jobs", "rows": [_job(1), _job(2, status="running", started_at=5.0)], "cursor": 0})
        devices = _snapshot_seen(client)
        _until(lambda: farm.get_hub(app).store.cursor() == 102.0)
        status = client.get("/api/farm/status").json()
        assert status["online"] and status["connected"] and status["agent_version"] == "1.0"
        assert status["scheduler_alive"] is True
    phone1 = next(d for d in client.get("/api/devices").json() if d["name"] == "phone1")
    assert phone1["status"]["info"]["battery"] == 80
    assert [a["id"] for a in phone1["accounts"]] == ["ig_fox"]
    assert phone1["current_job"]["id"] == 2 and phone1["next_job"]["id"] == 1
    assert [d["name"] for d in devices] == ["phone1", "phone2"]
    # reconnect: the cursor is what we already have
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as ws:
        assert _hello(ws, snapshot=False)["cursor"] == 102.0
        ws.send_json({"type": "jobs", "rows": [_job(1, status="done", updated_at=200.0)], "cursor": 102.0})
        _until(lambda: farm.get_hub(app).store.job(1)["status"] == "done")
    assert client.get("/api/farm/status").json()["connected"] is False


def test_command_queue_delivery_ack_and_resend(env):
    _, app, client = env
    _login(client)
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as ws:
        _hello(ws)
        _snapshot_seen(client)
    # offline: commands queue up
    r = client.post("/api/devices/phone1/pause", headers=H)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    pause_id = r.json()["command_id"]
    assert client.post("/api/devices/nope/pause", headers=H).status_code == 404
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as ws:
        _hello(ws, snapshot=False)
        cmd = ws.receive_json()
        assert cmd == {"type": "command", "command_id": pause_id, "kind": "device.pause", "args": {"device": "phone1"}}
        _until(lambda: client.get(f"/api/farm/commands/{pause_id}").json()["status"] == "sent")
    # never acked -> re-sent on reconnect, then the ack closes it
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as ws:
        _hello(ws, snapshot=False)
        again = ws.receive_json()
        assert again["command_id"] == pause_id
        ws.send_json({"type": "ack", "command_id": pause_id, "ok": True, "result": {"paused": True}, "error": None})
        done = _until(lambda: (c := client.get(f"/api/farm/commands/{pause_id}").json())["status"] == "done" and c)
        assert done["result"] == {"paused": True}
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as ws:
        _hello(ws, snapshot=False)
        ws.send_json({"type": "heartbeat", "ts": 1.0, "scheduler_alive": False, "scheduler_heartbeat_at": None})
        _until(lambda: client.get("/api/farm/status").json()["scheduler_alive"] is False)
        # sentinel: a fresh command must be the first frame after hello -> the acked pause was not re-sent
        r = client.post("/api/devices/phone1/resume", headers=H)
        assert r.status_code == 202
        nxt = ws.receive_json()
        assert nxt["command_id"] == r.json()["command_id"] and nxt["kind"] == "device.resume"
    cmds = {c["id"]: c for c in client.get("/api/farm/commands?limit=5").json()}
    assert cmds[pause_id]["status"] == "done" and cmds[pause_id]["result"] == {"paused": True}


def test_health_waits_for_ack(env):
    _, app, client = env
    _login(client)
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as ws:
        _hello(ws)
        _snapshot_seen(client)
        with ThreadPoolExecutor(1) as pool:
            fut = pool.submit(client.post, "/api/devices/phone1/health", headers=H)
            cmd = ws.receive_json()
            assert cmd["kind"] == "device.health"
            ws.send_json({"type": "ack", "command_id": cmd["command_id"], "ok": True, "result": STATUS[0], "error": None})
            r = fut.result(timeout=5)
        assert r.status_code == 200 and r.json()["result"]["adb_state"] == "device"
        # no ack in time -> 202
        farm.get_hub(app).ack_wait = 0.2
        r = client.post("/api/devices/phone1/screenshot", headers=H)
        assert r.status_code == 202 and r.json()["status"] == "sent"


def test_artifact_validation(env):
    settings, app, client = env
    _login(client)
    art_dir = settings.factory.data_dir / "farm" / "artifacts"
    png = base64.b64encode(b"\x89PNG fake").decode()
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as ws:
        _hello(ws)
        ws.send_json({"type": "artifact", "job_id": 7, "name": "../evil.png", "content_type": "image/png", "data_b64": png})
        ws.send_json({"type": "artifact", "job_id": 7, "name": "big.png", "content_type": "image/png",
                      "data_b64": base64.b64encode(b"x" * (farm.MAX_ARTIFACT + 1)).decode()})
        ws.send_json({"type": "artifact", "job_id": "7", "name": "s.png", "data_b64": png})
        ws.send_json({"type": "artifact", "job_id": 7, "name": "failure.png", "content_type": "image/png", "data_b64": png})
        _until(lambda: (art_dir / "7" / "failure.png").is_file())
    assert sorted(p.name for p in (art_dir / "7").iterdir()) == ["failure.png"]
    assert not (art_dir / "evil.png").exists()
    assert client.get("/api/farm/artifacts/7/failure.png").content == b"\x89PNG fake"
    assert client.get("/api/farm/artifacts/7/..%2F..%2Fstudio.db").status_code in (400, 404)


def test_accounts_validation_builds_config_update(env):
    _, app, client = env
    _login(client)
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as ws:
        _hello(ws)
        _snapshot_seen(client)
    good = {"id": "tt_fox", "platform": "tiktok", "username": "@fox", "device": "phone1",
            "mode": "audience", "sessions_per_day": "1-4", "working_hours": ["09:00-12:00"]}
    bad_cases = [
        ({**good, "platform": "myspace"}, 422), ({**good, "device": "phone9"}, 422),
        ({**good, "working_hours": ["9-12"]}, 422), ({**good, "sessions_per_day": 21}, 422),
        ({**good, "mode": "spam"}, 422), ({**good, "id": "bad id"}, 422),
        ({**good, "id": "ig_fox"}, 409), ({**good, "platform": "instagram", "id": "ig2"}, 409),
    ]
    for body, code in bad_cases:
        r = client.post("/api/accounts", headers=H, json=body)
        assert r.status_code == code, (body, r.text)
        assert isinstance(r.json()["detail"], str)
    farm.get_hub(app).ack_wait = 0.1
    r = client.post("/api/accounts", headers=H, json=good)
    assert r.status_code == 202
    cmd = client.get(f"/api/farm/commands/{r.json()['command_id']}").json()
    assert cmd["kind"] == "config.update" and list(cmd["args"]) == ["accounts"]
    accounts = cmd["args"]["accounts"]
    assert [a["id"] for a in accounts] == ["ig_fox", "tt_fox"]
    assert accounts[0]["custom"] == 1  # unknown fields survive the round trip
    assert accounts[1]["username"] == "fox" and accounts[1]["sessions_per_day"] == "1-4"
    # second offline edit builds on the queued one, not on the stale snapshot
    r = client.patch("/api/accounts/tt_fox", headers=H, json={"sessions_per_day": 5})
    accounts = client.get(f"/api/farm/commands/{r.json()['command_id']}").json()["args"]["accounts"]
    assert [a["sessions_per_day"] for a in accounts] == ["2-3", 5]
    assert client.patch("/api/accounts/tt_fox", headers=H, json={"id": "x"}).status_code == 422
    r = client.delete("/api/accounts/ig_fox", headers=H)
    accounts = client.get(f"/api/farm/commands/{r.json()['command_id']}").json()["args"]["accounts"]
    assert [a["id"] for a in accounts] == ["tt_fox"]
    assert client.delete("/api/accounts/ghost", headers=H).status_code == 404
    r = client.patch("/api/devices/phone2", headers=H, json={"require_vpn": True,
                                                             "pinned_apps": {"com.instagram.android": "440.0"}})
    devices = client.get(f"/api/farm/commands/{r.json()['command_id']}").json()["args"]["devices"]
    assert devices[1]["require_vpn"] is True and devices[1]["pinned_apps"] == {"com.instagram.android": "440.0"}
    assert client.patch("/api/devices/phone2", headers=H, json={"pinned_apps": {"bad pkg": "1"}}).status_code == 422


def test_failed_config_ack_is_409(env):
    _, app, client = env
    _login(client)
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as ws:
        _hello(ws)
        _snapshot_seen(client)
        with ThreadPoolExecutor(1) as pool:
            fut = pool.submit(client.delete, "/api/accounts/ig_fox", headers=H)
            cmd = ws.receive_json()
            ws.send_json({"type": "ack", "command_id": cmd["command_id"], "ok": False, "result": None,
                          "error": "yaml invalid"})
            r = fut.result(timeout=5)
    assert r.status_code == 409 and r.json()["error"] == "yaml invalid"


def test_publications_grid_and_retry_cancel(env):
    settings, app, client = env
    _login(client)
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as ws:
        _hello(ws)
        ws.send_json({"type": "jobs", "cursor": 0, "rows": [
            _job(1, status="failed", result={"error": "button not found", "url": "javascript:alert(1)"}),
            _job(2, status="done", result={"confirmed": True, "url": "https://instagram.com/p/1"}),
            _job(3, status="pending", account_id="tt_fox", release_id="rel-2"),
            _job(4, kind="warmup", release_id=None)]})
        _until(lambda: farm.get_hub(app).store.cursor() == 104.0)
        data = client.get("/api/publications").json()
        assert [r["release_id"] for r in data["rows"]] == ["rel-2", "rel-1"]
        cell = data["rows"][1]["cells"]["ig_fox"]
        assert cell["job_id"] == 2 and cell["confirmed"] and cell["attempts"] == 2
        assert cell["url"] == "https://instagram.com/p/1"
        failed = client.get("/api/publications?status=failed").json()["rows"][0]["cells"]["ig_fox"]
        assert failed["url"] is None  # non-http(s) links never reach <a href>
        assert client.get("/api/publications?status=failed").json()["rows"][0]["cells"]["ig_fox"]["error"] == "button not found"
        assert len(client.get("/api/publications?release_id=rel-2").json()["rows"]) == 1
        assert client.post("/api/publications/2/retry", headers=H).status_code == 409
        assert client.post("/api/publications/4/retry", headers=H).status_code == 404
        assert client.post("/api/publications/1/cancel", headers=H).status_code == 409
        with ThreadPoolExecutor(1) as pool:
            fut = pool.submit(client.post, "/api/publications/1/retry", headers=H)
            cmd = ws.receive_json()
            assert cmd["kind"] == "job.retry" and cmd["args"] == {"job_id": 1}
            ws.send_json({"type": "ack", "command_id": cmd["command_id"], "ok": True, "result": {"new_job_id": 9}})
            assert fut.result(timeout=5).json()["result"] == {"new_job_id": 9}
        farm.get_hub(app).ack_wait = 0.1
        assert client.post("/api/publications/3/cancel", headers=H).status_code == 202
        assert ws.receive_json()["kind"] == "job.cancel"


def test_publish_only_approved(env):
    settings, app, client = env
    _login(client)
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as ws:
        _hello(ws)
        _snapshot_seen(client)
    store = BriefStore(settings.factory.db_path)
    brief_id = _staged_release(settings, store)
    body = {"accounts": ["ig_fox"], "start_at": "2026-10-12T10:00:00+03:00", "spread_minutes": 30}
    assert client.post(f"/api/videos/{brief_id}/publish", headers=H, json=body).status_code == 409
    assert client.post("/api/videos/nope-1/publish", headers=H, json=body).status_code == 404
    store.move(brief_id, "approved")
    assert client.post(f"/api/videos/{brief_id}/publish", headers=H,
                       json={**body, "accounts": ["ghost"]}).status_code == 422
    r = client.post(f"/api/videos/{brief_id}/publish", headers=H, json=body)
    assert r.status_code == 202
    args = client.get(f"/api/farm/commands/{r.json()['command_id']}").json()["args"]
    assert args == {"release_id": brief_id, "accounts": ["ig_fox"], "start_at": "2026-10-12T10:00:00+03:00",
                    "spread_minutes": 30}


def test_live_socket_auth_and_relay(env):
    _, app, client = env
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as agent:
        _hello(agent)
        _until(lambda: farm.get_hub(app).store.devices())
        # no session cookie
        with client.websocket_connect("/api/devices/phone1/live", headers=ORIGIN) as ws:
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4401
        _login(client)
        # cross-site origin
        with client.websocket_connect("/api/devices/phone1/live", headers={"Origin": "https://evil.example"}) as ws:
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4403
        with client.websocket_connect("/api/devices/ghost/live", headers=ORIGIN) as ws:
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4404
        with client.websocket_connect("/api/devices/phone1/live", headers=ORIGIN) as ws:
            start = agent.receive_json()
            assert start["kind"] == "live.start" and start["args"]["device"] == "phone1"
            agent.send_json({"type": "frame", "device": "phone2", "ts": 1.0, "jpeg_b64": "nope"})
            agent.send_json({"type": "frame", "device": "phone1", "ts": 2.0, "jpeg_b64": "QUJD"})
            assert ws.receive_json() == {"type": "frame", "ts": 2.0, "jpeg_b64": "QUJD"}
            # the agent's 15-min budget ran out while the viewer still watches: re-armed
            agent.send_json({"type": "live.ended", "device": "phone1", "reason": "timeout"})
            again = agent.receive_json()
            assert again["kind"] == "live.start" and again["args"]["device"] == "phone1"
            # capture errors: no restart loop, the viewer is told
            agent.send_json({"type": "live.ended", "device": "phone1", "reason": "errors"})
            assert ws.receive_json() == {"type": "live.ended", "reason": "errors"}
        stop = agent.receive_json()
        assert stop["kind"] == "live.stop" and stop["args"] == {"device": "phone1"}
        assert farm.get_hub(app).viewers("phone1") == 0


def test_new_agent_replaces_old(env):
    _, app, client = env
    with client.websocket_connect("/api/farm/ws", headers=AGENT) as old:
        _hello(old, snapshot=False)
        with client.websocket_connect("/api/farm/ws", headers=AGENT) as new:
            _hello(new, snapshot=False)
            with pytest.raises(WebSocketDisconnect) as exc:
                old.receive_json()
            assert exc.value.code == 4000
            assert farm.get_hub(app).agent is not None
