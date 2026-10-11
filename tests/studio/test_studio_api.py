"""Studio API: auth (login, limiter, CSRF header), briefs, videos (caption edit, approve/reject)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factory import release
from factory.config import Settings
from factory.db import BriefStore
from studio import auth
from studio.app import create_app
from studio.config import StudioSettings

PASSWORD = "correct horse battery"
H = {"X-Studio": "1"}


def _settings(tmp_path: Path) -> StudioSettings:
    fs = Settings(repo=tmp_path, data_dir=tmp_path / "data", releases_dir=tmp_path / "data" / "releases",
                  claude_bin="claude", claude_proxy="", claude_max_turns=10, run_timeout_s=60,
                  tg_token="", tg_chat="", tg_proxy="", release_base_url="https://x/releases")
    return StudioSettings(factory=fs, admin_user="owner", admin_password_hash=auth.hash_password(PASSWORD),
                          secret="s" * 32, cookie_secure=False, session_days=1,
                          web_dist=tmp_path / "nodist", farm_agent_token="t")


@pytest.fixture
def env(tmp_path):
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))
    return settings, client


def _login(client):
    r = client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


def test_password_hash_roundtrip():
    h = auth.hash_password("pw-1234567890")
    assert auth.verify_password("pw-1234567890", h) and not auth.verify_password("nope", h)
    assert not auth.verify_password("x", "garbage")


def test_session_signature_and_expiry():
    tok = auth.sign_session("k", "owner", 60, now=1000)
    assert auth.read_session("k", tok, now=1001) == "owner"
    assert auth.read_session("k", tok, now=2000) is None
    assert auth.read_session("other", tok, now=1001) is None
    assert auth.read_session("k", tok[:-2] + "xx", now=1001) is None


def test_api_requires_login(env):
    _, client = env
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/auth/login", json={"username": "owner", "password": "bad"}).status_code == 401
    _login(client)
    assert client.get("/api/overview").status_code == 200
    assert client.get("/api/auth/me").json() == {"user": "owner"}


def test_empty_secret_rejects_forged_cookie(tmp_path):
    from dataclasses import replace
    settings = replace(_settings(tmp_path), secret="")
    client = TestClient(create_app(settings))
    client.cookies.set(auth.COOKIE, auth.sign_session("", "owner", 3600))
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/advertisers", json={"name": "x"}, headers=H).status_code == 401
    assert client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 503


def test_login_rate_limit(env):
    _, client = env
    for _ in range(auth.LOGIN_MAX_FAILS):
        client.post("/api/auth/login", json={"username": "owner", "password": "bad"})
    r = client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 429


def test_mutation_needs_csrf_header(env):
    _, client = env
    _login(client)
    body = {"text": "Кот-повар рекламирует пиццу, 30 секунд"}
    assert client.post("/api/briefs", json=body).status_code == 403
    r = client.post("/api/briefs", json=body, headers=H)
    assert r.status_code == 201


def test_create_and_list_brief(env):
    settings, client = env
    _login(client)
    r = client.post("/api/briefs", headers=H, json={"text": "Кот-повар рекламирует пиццу, 30 секунд",
                                                     "options": {"comment_bait": "meta_joke", "product": "пицца"}})
    brief_id = r.json()["id"]
    stored = BriefStore(settings.factory.db_path).get(brief_id)
    assert stored["state"] == "queued" and stored["options"] == {"comment_bait": "meta_joke", "product": "пицца"}
    rows = client.get("/api/briefs").json()
    assert rows[0]["id"] == brief_id and rows[0]["stages_total"] == 9
    detail = client.get(f"/api/briefs/{brief_id}").json()
    assert detail["events"][0]["state"] == "queued" and detail["stages"][0]["status"] == "pending"
    bad = client.post("/api/briefs", headers=H, json={"text": "достаточно длинный бриф", "options": {"comment_bait": "x"}})
    assert bad.status_code == 422


def test_cancel_and_retry(env):
    settings, client = env
    _login(client)
    store = BriefStore(settings.factory.db_path)
    brief_id = store.add("бриф для отмены и повтора")
    assert client.post(f"/api/briefs/{brief_id}/cancel", headers=H).status_code == 200
    assert store.get(brief_id)["state"] == "rejected"
    assert client.post(f"/api/briefs/{brief_id}/retry", headers=H).status_code == 409
    assert client.get("/api/briefs/../../etc").status_code in (400, 404)


def _staged_release(settings: StudioSettings, store: BriefStore) -> str:
    brief_id = store.add("бриф с готовым роликом")
    store.claim_next()
    pending = settings.factory.releases_dir / "pending" / brief_id
    pending.mkdir(parents=True)
    (pending / "final.mp4").write_bytes(b"video")
    (pending / "checkpoint_publish.json").write_text(json.dumps({"artifacts": {"publish_log": {"entries": [
        {"platform": "instagram", "export_path": "final.mp4",
         "metadata_used": {"description": "Старая подпись", "hashtags": ["кот"]}}]}}}), encoding="utf-8")
    staged = {"id": brief_id, "duration": 32.0, "caption": "Старая подпись", "hashtags": ["кот"]}
    store.move(brief_id, "awaiting_approval", detail=json.dumps(staged, ensure_ascii=False))
    return brief_id


def test_video_caption_edit_and_approve(env):
    settings, client = env
    _login(client)
    store = BriefStore(settings.factory.db_path)
    brief_id = _staged_release(settings, store)
    videos = client.get("/api/videos").json()
    assert videos[0]["id"] == brief_id and videos[0]["caption"] == "Старая подпись"
    r = client.patch(f"/api/videos/{brief_id}", headers=H, json={"caption": "Новая", "hashtags": ["#пицца", "кот"]})
    assert r.status_code == 200
    pub = json.loads((settings.factory.releases_dir / "pending" / brief_id / "checkpoint_publish.json").read_text())
    assert pub["artifacts"]["publish_log"]["entries"][0]["metadata_used"]["hashtags"] == ["пицца", "кот"]
    assert client.get(f"/api/videos/{brief_id}/file/final.mp4").content == b"video"
    assert client.get(f"/api/videos/{brief_id}/file/..%2F..%2Ffactory.db").status_code in (403, 404)
    assert client.post(f"/api/videos/{brief_id}/approve", headers=H).status_code == 200
    assert store.get(brief_id)["state"] == "approved"
    index = json.loads((settings.factory.releases_dir / "index.json").read_text())
    assert index["releases"][0]["id"] == brief_id
    # approved: caption is frozen, second approve conflicts
    assert client.patch(f"/api/videos/{brief_id}", headers=H, json={"caption": "x", "hashtags": []}).status_code == 409
    assert client.post(f"/api/videos/{brief_id}/approve", headers=H).status_code == 409


def test_video_reject(env):
    settings, client = env
    _login(client)
    store = BriefStore(settings.factory.db_path)
    brief_id = _staged_release(settings, store)
    assert client.post(f"/api/videos/{brief_id}/reject", headers=H).status_code == 200
    assert store.get(brief_id)["state"] == "rejected"
    assert not (settings.factory.releases_dir / "pending" / brief_id).exists()


def test_bot_and_web_share_actions(tmp_path):
    """Telegram ✅ goes through factory.actions too: approving twice is a clean error, not a crash."""
    from factory import service
    from tests.factory.test_factory import FakeTg

    settings = _settings(tmp_path)
    store = BriefStore(settings.factory.db_path)
    brief_id = _staged_release(settings, store)
    tg = FakeTg()
    upd = {"callback_query": {"id": "1", "data": f"approve:{brief_id}", "message": {"chat": {"id": 42}}}}
    service.handle_update(settings.factory, store, tg, upd)
    service.handle_update(settings.factory, store, tg, upd)
    assert store.get(brief_id)["state"] == "approved"
    assert tg.sent[0].startswith("✅") and tg.sent[1].startswith("⚠️")
