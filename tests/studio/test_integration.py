"""Studio integration: every add-on router is mounted and protected, brief options, factory hooks."""

import json

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient

from factory import release, runner, service
from factory.db import BriefStore
from studio.app import create_app
from tests.factory.test_factory import FakeTg, _project
from tests.studio.test_ads import _advertiser, _campaign, _insert
from tests.studio.test_studio_api import H, _login, _settings

PUBLIC = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/farm/ws"}


@pytest.fixture
def env(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    return settings, app, TestClient(app)


def _routes(routes, prefix=""):
    """Flatten app.routes (FastAPI >= 0.14x keeps included routers lazy) into (path, route)."""
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            ctx = getattr(r, "include_context", None)
            yield from _routes(inner.routes, prefix + (getattr(ctx, "prefix", "") or ""))
        elif isinstance(r, (APIRoute, APIWebSocketRoute)):
            yield prefix + r.path, r


def _concrete(path: str) -> str:
    out = []
    for part in path.split("/"):
        if part.startswith("{"):
            out.append("x.png" if ":path" in part else "x")
        else:
            out.append(part)
    return "/".join(out)


def test_all_api_routes_need_login(env):
    _, app, client = env
    routes = list(_routes(app.routes))
    paths = {p for p, _ in routes}
    for expected in ("/api/characters", "/api/advertisers", "/api/inserts", "/api/campaigns",
                     "/api/devices", "/api/accounts", "/api/publications", "/api/farm/ws",
                     "/api/devices/{name}/live", "/api/tasks/{task_id}", "/api/videos/{brief_id}/publish"):
        assert expected in paths, expected
    checked = 0
    for path, route in routes:
        if not isinstance(route, APIRoute) or not path.startswith("/api") or path in PUBLIC:
            continue
        url = _concrete(path)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            r = client.request(method, url, headers=H)
            assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
            checked += 1
    assert checked > 60


def test_live_socket_needs_cookie(env):
    from starlette.websockets import WebSocketDisconnect

    _, _, client = env
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/devices/p1/live") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_brief_form_characters_and_campaign(env):
    settings, _, client = env
    _login(client)
    adv = _advertiser(client)
    ins = _insert(client)
    cmp = _campaign(client, adv, ins)
    r = client.post("/api/briefs", headers=H, json={"text": "Лисёнок ищет пиццу в лесу",
                                                    "options": {"characters": " fox-1, ,cat_2 ", "campaign": cmp["id"]}})
    assert r.status_code == 201, r.text
    opts = BriefStore(settings.factory.db_path).get(r.json()["id"])["options"]
    assert opts["characters"] == "fox-1,cat_2"
    assert opts["campaign"] == cmp["id"] and opts["advertiser"] == adv["id"] and opts["insert"] == ins["id"]
    assert opts["insert_version"] == cmp["insert_version"] and opts["product"] == "пицца «Маргарита»"
    bad = client.post("/api/briefs", headers=H, json={"text": "Лисёнок ищет пиццу в лесу",
                                                      "options": {"campaign": "nope"}})
    assert bad.status_code == 422


def test_telegram_brief_carries_addon_options():
    text, opts = service.parse_brief("characters: fox-1,cat-2\ncampaign: cmp-1\nЛисёнок ищет сок в лесу")
    assert opts == {"characters": "fox-1,cat-2", "campaign": "cmp-1"}


def test_build_prompt_hooks(env, monkeypatch):
    settings, _, client = env
    _login(client)
    cmp = _campaign(client, _advertiser(client), _insert(client))
    brief = {"id": "b1", "text": "Бриф", "options": {"campaign": cmp["id"], "characters": "fox-1"}}
    p = runner.build_prompt(brief, [], settings.factory)
    assert "addons/ad-insert/README.md" in p and "fox-1: not found in the library" in p
    assert p.index("addons/ad-insert") < p.index("BRIEF (from the owner)")

    import studio.ads

    def boom(*a, **k):
        raise RuntimeError("hook down")

    monkeypatch.setattr(studio.ads, "prompt_lines", boom)
    p = runner.build_prompt(brief, [], settings.factory)  # a broken hook must not kill the run
    assert "fox-1" in p and "addons/ad-insert" not in p and "BRIEF (from the owner)" in p


def test_worker_appends_ad_marking(env, monkeypatch, tmp_path):
    settings, _, client = env
    _login(client)
    cmp = _campaign(client, _advertiser(client), _insert(client))
    s = settings.factory
    store = BriefStore(s.db_path)
    store.add("Лисёнок ищет пиццу в лесу", {"campaign": cmp["id"]}, brief_id="b1")
    proj = _project(tmp_path, "b1")
    monkeypatch.setattr(release, "check_video", lambda v, r=None: {"width": 1080, "height": 1920, "duration": 40.0})
    tg = FakeTg()
    service.process_one(s, store, tg, run=lambda st, b: runner.RunOutcome("READY", proj, "", 0))
    brief = store.get("b1")
    assert brief["state"] == "awaiting_approval"
    marking = "Реклама. ООО «Ромашка», ИНН 7700000000. erid: 2VtzqX"
    staged = json.loads(brief["detail"])
    assert staged["caption"] == f"Лис и МУЛЬТ\n\n{marking}"
    assert marking in tg.videos[0][1]
    cp = s.releases_dir / "pending" / "b1" / "checkpoint_publish.json"
    entry = json.loads(cp.read_text(encoding="utf-8"))["artifacts"]["publish_log"]["entries"][0]
    assert entry["metadata_used"]["description"] == f"Лис и МУЛЬТ\n\n{marking}"
    again = service.apply_ad_marking(s, brief, staged)  # idempotent
    assert again["caption"] == staged["caption"]
    entry = json.loads(cp.read_text(encoding="utf-8"))["artifacts"]["publish_log"]["entries"][0]
    assert entry["metadata_used"]["description"].count("Реклама.") == 1


def test_worker_marking_noop_for_plain_brief(tmp_path):
    from tests.factory.test_factory import _settings as fsettings

    s = fsettings(tmp_path)
    staged = {"caption": "x", "dir": str(tmp_path)}
    assert service.apply_ad_marking(s, {"options": {}}, staged) is staged


def test_studio_tasks_never_raise(tmp_path, monkeypatch):
    from studio import characters
    from tests.factory.test_factory import _settings as fsettings

    monkeypatch.setattr(characters, "run_pending_tasks", lambda s, limit=10: (_ for _ in ()).throw(OSError("x")))
    assert service.run_studio_tasks(fsettings(tmp_path)) == 0


def _staged_ad(env, monkeypatch, tmp_path, **campaign):
    settings, _, client = env
    _login(client)
    cmp = _campaign(client, _advertiser(client), _insert(client), **campaign)
    s = settings.factory
    store = BriefStore(s.db_path)
    store.add("Лисёнок ищет пиццу в лесу", {"campaign": cmp["id"]}, brief_id="b1")
    proj = _project(tmp_path, "b1")
    monkeypatch.setattr(release, "check_video", lambda v, r=None: {"width": 1080, "height": 1920, "duration": 40.0})
    service.process_one(s, store, FakeTg(), run=lambda st, b: runner.RunOutcome("READY", proj, "", 0))
    assert store.get("b1")["state"] == "awaiting_approval"
    return client, store, cmp


def _descriptions(root):
    data = json.loads((root / "checkpoint_publish.json").read_text(encoding="utf-8"))
    return [e["metadata_used"]["description"] for e in data["artifacts"]["publish_log"]["entries"]]


def test_erid_filled_after_staging_reaches_release(env, monkeypatch, tmp_path):
    client, store, cmp = _staged_ad(env, monkeypatch, tmp_path, erid="")
    s = env[0].factory
    old = "Реклама. ООО «Ромашка», ИНН 7700000000"
    assert _descriptions(s.releases_dir / "pending" / "b1") == [f"Лис и МУЛЬТ\n\n{old}"]
    # no ERID yet: approval is refused and the release stays pending
    r = client.post("/api/videos/b1/approve", headers=H)
    assert r.status_code == 409 and "ERID" in r.text
    assert store.get("b1")["state"] == "awaiting_approval"
    assert client.patch(f"/api/campaigns/{cmp['id']}", headers=H, json={"erid": "2VtzqX"}).status_code == 200
    assert client.post("/api/videos/b1/approve", headers=H).status_code == 200
    assert store.get("b1")["state"] == "approved"
    # the stale ERID-less line is replaced, not duplicated
    assert _descriptions(s.releases_dir / "b1") == [f"Лис и МУЛЬТ\n\n{old}. erid: 2VtzqX"]


def test_erid_not_required_allows_approval(env, monkeypatch, tmp_path):
    client, store, _ = _staged_ad(env, monkeypatch, tmp_path, erid="", erid_not_required=True)
    assert client.post("/api/videos/b1/approve", headers=H).status_code == 200


def test_caption_edit_keeps_ad_marking(env, monkeypatch, tmp_path):
    client, store, _ = _staged_ad(env, monkeypatch, tmp_path)
    marking = "Реклама. ООО «Ромашка», ИНН 7700000000. erid: 2VtzqX"
    r = client.patch("/api/videos/b1", headers=H, json={"caption": "Новая подпись", "hashtags": ["пицца"]})
    assert r.status_code == 200, r.text
    pending = env[0].factory.releases_dir / "pending" / "b1"
    assert _descriptions(pending) == [f"Новая подпись\n\n{marking}"]
    assert json.loads(store.get("b1")["detail"])["caption"] == f"Новая подпись\n\n{marking}"


def test_unresolved_ad_brief_fails_instead_of_unmarked(env, monkeypatch, tmp_path):
    settings, _, _ = env
    s = settings.factory
    store = BriefStore(s.db_path)
    store.add("Лисёнок ищет пиццу в лесу", {"campaign": "typo"}, brief_id="b2")
    proj = _project(tmp_path, "b2")
    monkeypatch.setattr(release, "check_video", lambda v, r=None: {"width": 1080, "height": 1920, "duration": 40.0})
    service.process_one(s, store, FakeTg(), run=lambda st, b: runner.RunOutcome("READY", proj, "", 0))
    brief = store.get("b2")
    assert brief["state"] == "failed" and brief["detail"].startswith("ad marking:")
