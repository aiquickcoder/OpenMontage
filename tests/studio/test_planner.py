"""Planner: episodes CRUD, Claude week plan, scheduled release into the factory queue."""

import json
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from factory.db import BriefStore
from studio import planner, worlds
from studio.app import create_app
from tests.studio.test_studio_api import H, _login, _settings


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("PLANNER_DAILY_LIMIT", raising=False)
    monkeypatch.delenv("PLANNER_RELEASE_HOUR_UTC", raising=False)
    settings = _settings(tmp_path)
    client = _login(TestClient(create_app(settings)))
    w = client.post("/api/worlds", headers=H, json={"name": "Кошачий квартал", "style_lock": "2D ink",
                                                    "duration": "30"}).json()
    client.post("/api/characters", headers=H, json={"name": "Мурка", "description": "Мама-кошка в фартуке",
                                                    "world_id": w["id"]})
    return settings, client, w["id"]


def _episode(client, world_id, air_date="2026-10-13", **extra):
    r = client.post("/api/episodes", headers=H, json={"world_id": world_id, "air_date": air_date,
                                                      "title": "Рыбный день", "synopsis": "1. Мурка: «Рыба!»", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_episode_crud(env):
    _, client, world_id = env
    ep = _episode(client, world_id, characters=["murka", "../x"])
    assert ep["status"] == "draft" and ep["characters"] == ["murka"] and ep["source"] == "manual"
    assert client.post("/api/episodes", headers=H, json={"world_id": "nope", "air_date": "2026-10-13",
                                                         "title": "x"}).status_code == 404
    r = client.patch(f"/api/episodes/{ep['id']}", headers=H, json={"status": "approved", "air_date": "2026-10-14"})
    assert r.json()["status"] == "approved" and r.json()["air_date"] == "2026-10-14"
    assert client.patch(f"/api/episodes/{ep['id']}", headers=H, json={"status": "queued"}).status_code == 422
    listed = client.get("/api/episodes?start=2026-10-12&end=2026-10-18").json()
    assert [e["id"] for e in listed] == [ep["id"]]
    assert client.get(f"/api/episodes?world={world_id}&status=draft").json() == []
    assert client.delete(f"/api/episodes/{ep['id']}", headers=H).json() == {"ok": True}


def test_release_due_respects_date_hour_and_limit(env):
    settings, client, world_id = env
    a = _episode(client, world_id, "2026-10-13", characters=["murka"])
    b = _episode(client, world_id, "2026-10-13")
    c = _episode(client, world_id, "2026-10-15")
    _episode(client, world_id, "2026-10-13")  # stays draft
    for ep in (a, b, c):
        client.patch(f"/api/episodes/{ep['id']}", headers=H, json={"status": "approved"})
    store = BriefStore(settings.factory.db_path)

    early = datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)
    assert planner.release_due(settings, store, early) == []
    now = datetime(2026, 10, 13, 6, 30, tzinfo=timezone.utc)
    released = planner.release_due(settings, store, now)
    assert len(released) == 1  # PLANNER_DAILY_LIMIT=1
    brief = store.get(released[0])
    assert brief["options"] == {"world": world_id, "episode": a["id"], "duration": "30", "characters": "murka"}
    assert brief["text"].startswith("Эпизод «Рыбный день».")
    assert client.get(f"/api/episodes?world={world_id}&status=queued").json()[0]["brief_id"] == released[0]
    assert planner.release_due(settings, store, now) == []  # today's slot is used

    nxt = planner.release_due(settings, store, datetime(2026, 10, 14, 7, tzinfo=timezone.utc))
    assert len(nxt) == 1 and store.get(nxt[0])["options"]["episode"] == b["id"]  # overdue first

    # status follows the brief; queued episodes are locked
    store.move(released[0], "running")
    store.move(released[0], "awaiting_approval")
    planner.sync_status(planner.EpisodeStore.of(settings), store)
    assert client.get(f"/api/episodes?status=done").json()[0]["id"] == a["id"]
    assert client.patch(f"/api/episodes/{a['id']}", headers=H, json={"title": "x"}).status_code == 409
    assert client.delete(f"/api/episodes/{a['id']}", headers=H).status_code == 409


def test_release_limit_from_env(env, monkeypatch):
    settings, client, world_id = env
    monkeypatch.setenv("PLANNER_DAILY_LIMIT", "2  # two a day")
    monkeypatch.setenv("PLANNER_RELEASE_HOUR_UTC", "0")
    for _ in range(3):
        ep = _episode(client, world_id, "2026-10-13")
        client.patch(f"/api/episodes/{ep['id']}", headers=H, json={"status": "approved"})
    store = BriefStore(settings.factory.db_path)
    assert len(planner.release_due(settings, store, datetime(2026, 10, 13, 0, 5, tzinfo=timezone.utc))) == 2


def test_plan_week_with_claude(env, monkeypatch):
    settings, client, world_id = env
    _episode(client, world_id, title="Старый эпизод", logline="уже было")
    seen = {}

    def fake(s, prompt, **k):
        seen["prompt"] = prompt
        return {"episodes": [{"title": f"Эпизод {i}", "logline": "л", "synopsis": "1. «Мяу»",
                              "characters": ["murka", "ghost"]} for i in range(9)]}

    monkeypatch.setattr(worlds, "ask_claude_json", fake)
    result = planner.plan_week(settings, world_id, date(2026, 10, 13), 7, "больше про рыбу")
    assert len(result["episodes"]) == 7
    assert "Старый эпизод: уже было" in seen["prompt"] and "больше про рыбу" in seen["prompt"] and "murka" in seen["prompt"]
    eps = client.get(f"/api/episodes?world={world_id}&start=2026-10-13&end=2026-10-19").json()
    ai = [e for e in eps if e["source"] == "ai"]
    assert [e["air_date"] for e in ai] == [f"2026-10-{d}" for d in range(13, 20)]
    assert all(e["characters"] == ["murka"] and e["status"] == "draft" for e in ai)


def test_plan_week_route_needs_characters(env, monkeypatch):
    settings, client, _ = env
    empty = client.post("/api/worlds", headers=H, json={"name": "Пустой мир"}).json()
    r = client.post(f"/api/worlds/{empty['id']}/plan-week", headers=H, json={"start_date": "2026-10-13"})
    assert r.status_code == 422


def test_budget_stopped_episode_resumes_next_day(env):
    settings, client, world_id = env
    a = _episode(client, world_id, "2026-10-13")
    b = _episode(client, world_id, "2026-10-14")
    for ep in (a, b):
        client.patch(f"/api/episodes/{ep['id']}", headers=H, json={"status": "approved"})
    store = BriefStore(settings.factory.db_path)
    day1 = planner.release_due(settings, store, datetime(2026, 10, 13, 7, tzinfo=timezone.utc))
    store.move(day1[0], "running")
    store.move(day1[0], "budget")
    assert planner.release_due(settings, store, datetime(2026, 10, 13, 20, tzinfo=timezone.utc)) == []
    day2 = planner.release_due(settings, store, datetime(2026, 10, 14, 7, tzinfo=timezone.utc))
    assert day2 == day1 and store.get(day1[0])["state"] == "queued"  # yesterday's episode continues
    eps = planner.EpisodeStore.of(settings)
    assert eps.get(a["id"])["status"] == "queued" and eps.get(b["id"])["status"] == "approved"  # slot used
    assert planner.release_due(settings, store, datetime(2026, 10, 14, 9, tzinfo=timezone.utc)) == []


def test_old_episodes_table_is_migrated(tmp_path):
    import sqlite3

    db = tmp_path / "studio.db"
    with sqlite3.connect(db) as c:  # the table as deployed before resumed_at existed
        c.execute("CREATE TABLE episodes (id TEXT PRIMARY KEY, world_id TEXT, air_date TEXT, title TEXT, logline TEXT,"
                  " synopsis TEXT, characters TEXT, notes TEXT, status TEXT, source TEXT, brief_id TEXT,"
                  " released_at TEXT, created_at TEXT, updated_at TEXT)")
    assert planner.EpisodeStore(db).released_on("2026-10-15") == 0


def test_per_world_cap_and_format(env, monkeypatch):
    settings, client, world_a = env
    monkeypatch.setenv("PLANNER_DAILY_LIMIT", "2")
    monkeypatch.setenv("PLANNER_DAILY_LIMIT_PER_WORLD", "1")
    world_b = client.post("/api/worlds", headers=H, json={"name": "Россия 90-х"}).json()["id"]
    wfile = worlds.worlds_dir(settings) / world_b / "world.json"
    data = json.loads(wfile.read_text(encoding="utf-8"))
    wfile.write_text(json.dumps({**data, "format": "mural"}, ensure_ascii=False), encoding="utf-8")
    for world_id in (world_a, world_a, world_b, world_b):
        ep = _episode(client, world_id, "2026-10-13")
        client.patch(f"/api/episodes/{ep['id']}", headers=H, json={"status": "approved"})
    store = BriefStore(settings.factory.db_path)
    day1 = planner.release_due(settings, store, datetime(2026, 10, 13, 7, tzinfo=timezone.utc))
    opts = [store.get(b)["options"] for b in day1]
    assert sorted(o["world"] for o in opts) == sorted([world_a, world_b])  # one per world, not two of the first
    assert [o.get("format") for o in opts if o["world"] == world_b] == ["mural"]
    assert all("format" not in o for o in opts if o["world"] == world_a)
    assert planner.release_due(settings, store, datetime(2026, 10, 13, 9, tzinfo=timezone.utc)) == []
    assert len(planner.release_due(settings, store, datetime(2026, 10, 14, 7, tzinfo=timezone.utc))) == 2
