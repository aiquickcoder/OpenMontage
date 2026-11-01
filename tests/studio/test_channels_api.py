"""Studio «Каналы»: login required, channel settings, sources add/edit/delete, posts take-off, settings whitelist."""
import time

import pytest
from fastapi.testclient import TestClient

from addons.channels import config
from addons.channels.db import DB
from studio.app import create_app
from tests.studio.test_studio_api import H, _login, _settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "channels.db")
    return _login(TestClient(create_app(_settings(tmp_path))))


def test_channels_need_login(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "channels.db")
    c = TestClient(create_app(_settings(tmp_path)))
    assert c.get("/api/channels/overview").status_code == 401


def test_overview_lists_three_channels_in_trial(client):
    data = client.get("/api/channels/overview").json()
    assert [c["key"] for c in data["channels"]] == ["news", "moscow", "kazan"]
    assert all(c["mode"] == "trial" for c in data["channels"])
    assert data["sources"]["n"] > 80


def test_patch_channel_rate_and_pause_but_live_needs_rights(client, monkeypatch):
    r = client.patch("/api/channels/ch/moscow", json={"per_day": 20, "paused": True}, headers=H)
    assert r.status_code == 200 and r.json()["per_day"] == 20 and r.json()["paused"] == 1
    from addons.channels import publish

    monkeypatch.setattr(publish, "check_rights", lambda ch: {"ok": False, "detail": "not admin"})
    r = client.patch("/api/channels/ch/moscow", json={"mode": "live"}, headers=H)
    assert r.status_code == 409 and "not admin" in r.json()["detail"]
    assert client.patch("/api/channels/ch/moscow", json={"per_day": 5}).status_code == 403  # no X-Studio


def test_sources_add_edit_delete(client):
    r = client.post("/api/channels/sources", json={"platform": "telegram", "ref": "https://t.me/new_channel",
                                                   "region": "kazan", "kind": "city", "trust": 0.5}, headers=H)
    assert r.status_code == 201
    sid = r.json()["id"]
    assert client.post("/api/channels/sources", json={"platform": "telegram", "ref": "@new_channel"},
                       headers=H).status_code == 409
    assert client.patch(f"/api/channels/sources/{sid}", json={"trust": 0.9, "enabled": False}, headers=H).json()["trust"] == 0.9
    assert client.delete(f"/api/channels/sources/{sid}", headers=H).status_code == 200
    assert all(s["ref"] != "new_channel" for s in client.get("/api/channels/sources").json())


def test_posts_take_off_and_settings_whitelist(client):
    d = DB(config.DB_PATH)
    pid = d.insert("posts", {"story_id": 1, "channel": "news", "status": "ready", "created": time.time(),
                             "updated": time.time(), "html": "⚡️<b>x</b>"})
    assert client.get("/api/channels/posts?status=ready").json()[0]["id"] == pid
    assert "Снято" in client.post(f"/api/channels/posts/{pid}/delete", headers=H).json()["note"]
    assert client.put("/api/channels/settings", json={"values": {"evil": 1}}, headers=H).status_code == 422
    s = client.put("/api/channels/settings", json={"values": {"daily_budget_usd": 3}}, headers=H).json()
    assert s["daily_budget_usd"] == 3 and "ЖЁСТКИЕ ПРАВИЛА" in s["style_common"]
