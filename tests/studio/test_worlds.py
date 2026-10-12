"""Worlds: CRUD, references, archive import, Claude extraction, world characters, prompt hook."""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from factory import runner
from studio import characters, worlds
from studio.app import create_app
from tests.studio.test_studio_api import H, _login, _settings

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def env(tmp_path):
    settings = _settings(tmp_path)
    return settings, _login(TestClient(create_app(settings)))


def _world(client, **extra):
    body = {"name": "Кошачий квартал", "style_lock": "2D hand-drawn ink and gouache, warm ochre palette", **extra}
    r = client.post("/api/worlds", headers=H, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


MANIFEST = [
    {"n": 1, "name": "01-urban.png", "label": "01 · ГОРОДСКАЯ КОМЕДИЯ", "prompt": "Mother ... Father ...",
     "reference_files": ["references/style.png"]},
    {"n": 2, "name": "../../evil.png", "label": "02 · ЛЕСНЫЕ ЧУДИКИ", "prompt": "forest", "reference_files": []},
]


def test_world_crud_and_refs(env):
    settings, client = env
    w = _world(client)
    assert w["id"] == "kosacii_kvartal" and w["character_count"] == 0 and w["characters"] == []
    assert (settings.worlds_dir / w["id"] / "world.json").is_file()

    r = client.patch(f"/api/worlds/{w['id']}", headers=H, json={"logline": "Кошки живут как люди", "duration": "30"})
    assert r.json()["logline"] == "Кошки живут как люди" and r.json()["duration"] == "30"

    r = client.post(f"/api/worlds/{w['id']}/image?kind=style&filename=look.png", headers=H, content=PNG)
    assert r.status_code == 200, r.text
    ref = r.json()["path"]
    assert ref.startswith("refs/style-look-") and r.json()["world"]["refs"][0]["kind"] == "style"
    assert client.post(f"/api/worlds/{w['id']}/image?kind=cover", headers=H, content=PNG).json()["world"]["cover"]
    assert client.post(f"/api/worlds/{w['id']}/image?kind=bogus", headers=H, content=PNG).status_code == 422
    assert client.post(f"/api/worlds/{w['id']}/image?kind=style", headers=H, content=b"GIF89a").status_code == 415

    assert client.get(f"/api/worlds/{w['id']}/file/{ref}").status_code == 200
    assert client.get(f"/api/worlds/{w['id']}/file/../../studio.db").status_code in (403, 404)
    assert client.delete(f"/api/worlds/{w['id']}/refs/{ref}", headers=H).json()["refs"] == []
    assert client.delete(f"/api/worlds/{w['id']}/refs/{ref}", headers=H).status_code == 404

    assert client.patch(f"/api/worlds/{w['id']}", headers=H, json={"archived": True}).json()["archived"] is True
    assert client.get("/api/worlds").json() == []
    assert [x["id"] for x in client.get("/api/worlds?archived=1").json()] == [w["id"]]
    assert client.get("/api/worlds/nope").status_code == 404


def test_import_archive_is_safe(env):
    settings, client = env
    data = _zip({"generation-manifest.json": json.dumps(MANIFEST).encode(), "01-urban.png": PNG,
                 "references/style.png": PNG, "evil.png": PNG})
    r = client.post("/api/worlds/import?extract=0", headers=H, content=data)
    assert r.status_code == 201, r.text
    ids = r.json()["ids"]
    assert ids == ["gorodskaa_komedia", "lesnye_cudiki"]
    urban = client.get("/api/worlds/gorodskaa_komedia").json()
    assert urban["name"] == "Городская комедия" and urban["cover"] and urban["source_prompt"].startswith("Mother")
    assert sorted(r["kind"] for r in urban["refs"]) == ["family", "style"]
    forest = client.get("/api/worlds/lesnye_cudiki").json()
    assert forest["cover"] == "" and forest["refs"] == []  # "../../evil.png" was never read
    assert not (settings.factory.data_dir.parent / "evil.png").exists()

    assert client.post("/api/worlds/import?extract=0", headers=H, content=b"not a zip").status_code == 422
    no_manifest = _zip({"a.png": PNG})
    assert client.post("/api/worlds/import?extract=0", headers=H, content=no_manifest).status_code == 422


def test_extract_and_apply_draft(env, monkeypatch):
    settings, client = env
    data = _zip({"generation-manifest.json": json.dumps(MANIFEST[:1]).encode(), "01-urban.png": PNG})
    world_id = client.post("/api/worlds/import?extract=0", headers=H, content=data).json()["ids"][0]
    answer = {"name": "Городская комедия", "logline": "Семья и магазин", "style_lock": "2D sepia line art",
              "negative_style": "no 3D render, no text", "setting": "grocery", "episode_rules": "1 шутка",
              "characters": [{"name": "Вера", "role": "mother", "description": "Мама с каре и серебряной прядью",
                              "prompt_tokens": "Vera, pear-shaped mother, black bob with silver streak",
                              "voice": {"language": "ru", "description": "женский, ироничный"}},
                             {"bogus": True}]}
    seen = {}
    monkeypatch.setattr(worlds, "ask_claude_json", lambda s, prompt, **k: seen.setdefault("p", prompt) and answer)
    draft = worlds.extract_world(settings, worlds.WorldStore.of(settings), world_id)
    assert "Mother ..." in seen["p"] and len(draft["characters"]) == 1
    assert client.get(f"/api/worlds/{world_id}").json()["draft"]["style_lock"] == "2D sepia line art"

    body = {**{k: draft[k] for k in ("style_lock", "negative_style", "logline")},
            "characters": [{**draft["characters"][0], "generate": True}], "variants": 2}
    r = client.post(f"/api/worlds/{world_id}/apply-draft", headers=H, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["characters"] == ["vera"] and r.json()["world"]["draft"] is None
    vera = client.get("/api/characters/vera").json()
    assert vera["library"]["world_id"] == world_id and vera["library"]["style_lock"] == "2D sepia line art"
    assert vera["task"]["status"] == "queued"
    assert [c["id"] for c in client.get(f"/api/characters?world={world_id}").json()] == ["vera"]
    assert client.get(f"/api/worlds/{world_id}").json()["characters"][0]["id"] == "vera"

    # the sheet is drawn against the world's family image
    lib = characters.Library.of(settings)
    refs = characters.sheet_refs(lib, lib.load("vera"))
    assert refs and all("/worlds/" + world_id + "/refs/" in r for r in refs)


def test_character_world_link_validated(env):
    _, client = env
    w = _world(client)
    r = client.post("/api/characters", headers=H, json={"name": "Мурка", "description": "Мама-кошка в фартуке",
                                                        "world_id": w["id"]})
    assert r.status_code == 201 and r.json()["library"]["style_lock"].startswith("2D hand-drawn")
    assert client.post("/api/characters", headers=H, json={"name": "X", "description": "нет такого мира",
                                                           "world_id": "nope"}).status_code == 404
    assert "world_id" not in client.patch("/api/characters/murka", headers=H, json={"world_id": ""}).json()["library"]


def test_worker_never_claims_studio_claude_tasks(env):
    settings, _ = env
    lib = characters.Library.of(settings)
    lib.add_task("week_plan", {}, status="running")
    lib.add_task("week_plan", {})  # even a queued non-Flow task is not the worker's
    assert lib.claim_task() is None
    flow = lib.add_task("character_generate", {"character_id": "x"})
    assert lib.claim_task()["id"] == flow


def test_prompt_lines_for_world_brief(env):
    settings, client = env
    w = _world(client)
    client.post("/api/characters", headers=H, json={"name": "Мурка", "description": "Мама-кошка в фартуке",
                                                    "world_id": w["id"]})
    client.post("/api/characters/murka/image?kind=turnaround", headers=H, content=PNG)
    prompt = runner.build_prompt({"id": "b1", "text": "Эпизод", "options": {"world": w["id"], "episode": "ep1"}},
                                 settings=settings.factory)
    assert "addons/series/README.md" in prompt and f"{w['id']}/world.json" in prompt
    assert "Episode id: ep1" in prompt and "murka:" in prompt and "addons/character-library" in prompt
    assert worlds.prompt_lines(settings, {"world": "nope"})[0].startswith("- World `nope` not found")
    assert worlds.prompt_lines(settings, {}) == []


def test_brief_with_world(env):
    _, client = env
    w = _world(client)
    r = client.post("/api/briefs", headers=H, json={"text": "Эпизод про кошек и рыбу", "options": {"world": w["id"]}})
    assert r.status_code == 201, r.text
    assert client.post("/api/briefs", headers=H, json={"text": "Эпизод про кошек и рыбу",
                                                       "options": {"world": "nope"}}).status_code == 404
    r = client.post("/api/briefs", headers=H, json={"text": "Эпизод про кошек и рыбу",
                                                    "options": {"world": w["id"], "campaign": "c1"}})
    assert r.status_code == 422
