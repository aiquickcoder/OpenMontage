"""Character library: CRUD/archive, uploads, copy from a project, Flow generation tasks, prompt hook."""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from studio import characters
from studio.app import create_app
from tests.studio.test_studio_api import H, _login, _settings

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32

CAT = {"name": "Кот Барсик", "role": "hero", "description": "Рыжий пухлый кот в поварском колпаке и фартуке",
       "prompt_tokens": "Barsik, chubby ginger cat in a chef hat", "voice": {"language": "ru", "description": "мужской, бархатный"},
       "style_lock": "3D Pixar-like style, soft light, saturated colors", "tags": ["кот", "#повар"]}


@pytest.fixture
def env(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    app.include_router(characters.router)
    return settings, _login(TestClient(app))


def _create(client, **extra):
    r = client.post("/api/characters", headers=H, json={**CAT, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_crud_search_and_archive(env):
    settings, client = env
    cat = _create(client)
    assert cat["id"] == "kot_barsik" and cat["library"]["source"] == "manual"
    assert cat["library"]["tags"] == ["кот", "повар"] and cat["voice"]["description"] == "мужской, бархатный"
    on_disk = json.loads((settings.characters_dir / "kot_barsik" / "character.json").read_text())
    assert on_disk["prompt_tokens"] == CAT["prompt_tokens"]
    assert _create(client)["id"] == "kot_barsik_2"  # same name -> unique id

    assert sorted(c["id"] for c in client.get("/api/characters?q=КОЛПАК").json()) == ["kot_barsik", "kot_barsik_2"]
    assert client.get("/api/characters?q=собака").json() == []
    assert len(client.get("/api/characters?tag=повар").json()) == 2

    r = client.patch("/api/characters/kot_barsik", headers=H,
                     json={"name": "Барсик", "style_lock": "2D flat", "tags": ["x"], "voice": {"language": "en", "description": "deep"}})
    assert r.status_code == 200 and r.json()["name"] == "Барсик" and r.json()["library"]["style_lock"] == "2D flat"
    assert client.get("/api/characters/kot_barsik").json()["voice"] == {"language": "en", "description": "deep"}

    assert client.delete("/api/characters/kot_barsik", headers=H).status_code == 200
    assert (settings.characters_dir / "kot_barsik" / "character.json").is_file()  # archived, not deleted
    assert [c["id"] for c in client.get("/api/characters").json()] == ["kot_barsik_2"]
    assert [c["id"] for c in client.get("/api/characters?archived=1").json()] == ["kot_barsik"]
    assert len(client.get("/api/characters?archived=all").json()) == 2
    client.patch("/api/characters/kot_barsik", headers=H, json={"archived": False})
    assert len(client.get("/api/characters").json()) == 2

    assert client.get("/api/characters/nope").status_code == 404
    assert client.get("/api/characters/..%2F..%2Fetc").status_code in (400, 404)
    assert client.post("/api/characters", json=CAT).status_code == 403  # CSRF header


def test_index_rebuilds_from_disk(env):
    settings, client = env
    _create(client)
    with sqlite3.connect(settings.db_path) as c:
        c.execute("DROP TABLE characters")
    lib = characters.Library.of(settings)
    assert [c["id"] for c in lib.search()] == ["kot_barsik"]


def test_upload_validation(env):
    settings, client = env
    cat = _create(client)
    url = f"/api/characters/{cat['id']}/image"
    r = client.post(url, headers=H, data={"kind": "turnaround"}, files={"file": ("sheet.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "turnaround.png" and r.json()["character"]["turnaround_path"] == "turnaround.png"
    d = settings.characters_dir / cat["id"]
    assert (d / "turnaround.png").read_bytes() == PNG
    # replacing with a jpg removes the old png
    client.post(url, headers=H, data={"kind": "turnaround"}, files={"file": ("x.jpg", JPG, "image/jpeg")})
    assert not (d / "turnaround.png").exists() and (d / "turnaround.jpg").is_file()
    # raw body variant
    r = client.post(f"{url}?kind=expression", headers={**H, "Content-Type": "image/webp"}, content=WEBP)
    assert r.status_code == 200 and r.json()["path"] == "expression.webp"
    assert client.get(f"/api/characters/{cat['id']}").json()["expression_sheet_path"] == "expression.webp"

    # traversal filename is neutralised
    r = client.post(url, headers=H, data={"kind": "ref"}, files={"file": ("../../evil.png", PNG, "image/png")})
    rel = r.json()["path"]
    assert rel.startswith("refs/evil-") and rel.endswith(".png") and (d / rel).is_file()
    assert client.get(f"/api/characters/{cat['id']}").json()["files"]["refs"] == [rel]
    assert not (settings.characters_dir.parent / "evil.png").exists()

    bad = client.post(url, headers=H, data={"kind": "ref"}, files={"file": ("x.png", b"<?php echo 1;", "image/png")})
    assert bad.status_code == 415
    gif = client.post(url, headers=H, data={"kind": "ref"}, files={"file": ("x.gif", b"GIF89a" + b"\0" * 20, "image/gif")})
    assert gif.status_code == 415
    assert client.post(url, headers=H, data={"kind": "avatar"}, files={"file": ("x.png", PNG, "image/png")}).status_code == 422
    big = PNG + b"\0" * (characters.MAX_UPLOAD + 1)
    assert client.post(url, headers=H, data={"kind": "ref"}, files={"file": ("x.png", big, "image/png")}).status_code == 413

    # file serving stays inside the character dir
    assert client.get(f"/api/characters/{cat['id']}/file/turnaround.jpg").content == JPG
    assert client.get(f"/api/characters/{cat['id']}/file/character.json").status_code == 404
    assert client.get(f"/api/characters/{cat['id']}/file/..%2F..%2Fstudio.db").status_code in (403, 404)


def _project(settings, nested: bool, in_checkpoint: bool) -> str:
    pid = "20260101-000000-pizza"
    outer = settings.factory.projects_dir / pid
    board = outer / pid if nested else outer
    sheet = outer / "assets" / "characters" / "hero_cat" / "turnaround.png"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(PNG)
    bible = {"version": "1.0", "style_lock": "Pixar-like 3D, warm kitchen light, saturated",
             "characters": [{"id": "hero_cat", "name": "Барсик", "role": "hero", "description": "рыжий кот",
                             "turnaround_path": "assets/characters/hero_cat/turnaround.png",
                             "expression_sheet_path": "../../outside.png",
                             "voice": {"language": "ru", "description": "бархатный бас"},
                             "prompt_tokens": "Barsik the ginger cat", "flow_media_ids": ["m-1"]}]}
    board.mkdir(parents=True, exist_ok=True)
    if in_checkpoint:
        (board / "checkpoint_character_design.json").write_text(json.dumps({"artifacts": {"character_bible": bible}}))
    else:
        (board / "artifacts").mkdir()
        (board / "artifacts" / "character_bible.json").write_text(json.dumps(bible))
    return pid


@pytest.mark.parametrize("nested,in_checkpoint", [(False, False), (True, True)])
def test_from_project(env, nested, in_checkpoint):
    settings, client = env
    (settings.factory.repo / "outside.png").write_bytes(PNG)
    pid = _project(settings, nested, in_checkpoint)
    r = client.post("/api/characters/from-project", headers=H, json={"project_id": pid, "character_id": "hero_cat"})
    assert r.status_code == 201, r.text
    char = r.json()
    assert char["id"] == "hero_cat" and char["library"]["source"] == f"project:{pid}"
    assert char["library"]["style_lock"].startswith("Pixar-like") and char["voice"]["description"] == "бархатный бас"
    assert char["turnaround_path"] == "turnaround.png" and char["flow_media_ids"] == ["m-1"]
    assert "expression_sheet_path" not in char  # path escaping the project is ignored
    assert (settings.characters_dir / "hero_cat" / "turnaround.png").read_bytes() == PNG
    again = client.post("/api/characters/from-project", headers=H, json={"brief_id": pid, "character_id": "hero_cat"})
    assert again.json()["id"] == "hero_cat_2"
    assert client.post("/api/characters/from-project", headers=H,
                       json={"project_id": pid, "character_id": "nobody"}).status_code == 404
    assert client.post("/api/characters/from-project", headers=H, json={"character_id": "x"}).status_code == 422


class FakeFlow:
    calls: list = []
    fail = False

    def execute(self, inputs):
        FakeFlow.calls.append(inputs)
        if FakeFlow.fail:
            return SimpleNamespace(success=False, error="PUBLIC_ERROR_X", data={}, artifacts=[])
        out = Path(inputs["output_path"])
        paths = [out] + [out.with_name(f"1_{i}.png") for i in range(2, inputs["number_of_images"] + 1)]
        for i, p in enumerate(paths):
            p.write_bytes(PNG + bytes([i]))
        return SimpleNamespace(success=True, data={"media_ids": [f"m{i}" for i in range(len(paths))]},
                               artifacts=[str(p) for p in paths])


def test_generate_task_lifecycle(env, monkeypatch):
    settings, client = env
    monkeypatch.setattr(characters, "_flow_image", FakeFlow)
    FakeFlow.calls, FakeFlow.fail = [], False
    r = client.post("/api/characters/generate", headers=H,
                    json={"name": "Сова Ухта", "description": "Мудрая сова в очках и шарфе", "role": "sidekick",
                          "voice": {"language": "ru", "description": "старческий, неторопливый"}, "variants": 3})
    assert r.status_code == 201, r.text
    char_id, task_id = r.json()["id"], r.json()["task_id"]
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "queued"
    assert client.get(f"/api/characters/{char_id}").json()["task"]["status"] == "queued"

    assert characters.run_pending_tasks(settings) == 1
    assert characters.run_pending_tasks(settings) == 0
    call = FakeFlow.calls[0]
    assert call["number_of_images"] == 3 and call["model"] == "nano-pro" and call["aspect_ratio"] == "16:9"
    assert call["scene_id"] == f"char_{char_id}" and "three-quarter" in call["prompt"] and "No text" in call["prompt"]
    assert call["prompt"].startswith(characters.DEFAULT_STYLE)
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "done" and task["result"]["variants"] == ["variants/1.png", "variants/2.png", "variants/3.png"]
    assert client.get(f"/api/characters/{char_id}").json()["files"]["variants"] == task["result"]["variants"]

    r = client.post(f"/api/characters/{char_id}/choose-variant", headers=H, json={"variant": 2})
    assert r.status_code == 200 and r.json()["turnaround_path"] == "turnaround.png"
    assert r.json()["flow_media_ids"] == ["m1"]
    assert (settings.characters_dir / char_id / "turnaround.png").read_bytes() == PNG + b"\x01"
    assert client.post(f"/api/characters/{char_id}/choose-variant", headers=H, json={"variant": 4}).status_code == 404

    FakeFlow.fail = True
    task2 = client.post("/api/characters/generate", headers=H, json={**CAT, "variants": 1}).json()["task_id"]
    assert characters.run_pending_tasks(settings) == 1
    failed = client.get(f"/api/tasks/{task2}").json()
    assert failed["status"] == "failed" and "PUBLIC_ERROR_X" in failed["error"]
    assert client.get("/api/tasks/nope").status_code == 404

    # regenerate after a failure; a second request while queued conflicts
    cat_id = client.get("/api/characters?q=барсик").json()[0]["id"]
    FakeFlow.fail = False
    r = client.post(f"/api/characters/{cat_id}/generate", headers=H, json={"variants": 1})
    assert r.status_code == 201 and r.json()["character"]["task"]["status"] == "queued"
    assert client.post(f"/api/characters/{cat_id}/generate", headers=H, json={"variants": 1}).status_code == 409
    assert characters.run_pending_tasks(settings) == 1
    assert client.get(f"/api/characters/{cat_id}").json()["files"]["variants"] == ["variants/1.png"]

    # a failed regeneration keeps the previous variants (owner can still choose one)
    FakeFlow.fail = True
    assert client.post(f"/api/characters/{char_id}/generate", headers=H, json={"variants": 2}).status_code == 201
    assert characters.run_pending_tasks(settings) == 1
    assert client.get(f"/api/characters/{char_id}").json()["task"]["status"] == "failed"
    assert client.get(f"/api/characters/{char_id}").json()["files"]["variants"] == task["result"]["variants"]
    assert not (settings.characters_dir / char_id / "variants.new").exists()
    assert client.post(f"/api/characters/{char_id}/choose-variant", headers=H, json={"variant": 3}).status_code == 200

    # an exception from Flow also keeps them; a later success replaces the whole set
    class Boom:
        def execute(self, inputs):
            Path(inputs["output_path"]).write_bytes(PNG)
            raise RuntimeError("flow crashed")
    monkeypatch.setattr(characters, "_flow_image", Boom)
    client.post(f"/api/characters/{char_id}/generate", headers=H, json={"variants": 2})
    assert characters.run_pending_tasks(settings) == 1
    assert client.get(f"/api/characters/{char_id}").json()["files"]["variants"] == task["result"]["variants"]
    assert not (settings.characters_dir / char_id / "variants.new").exists()
    monkeypatch.setattr(characters, "_flow_image", FakeFlow)
    FakeFlow.fail = False
    client.post(f"/api/characters/{char_id}/generate", headers=H, json={"variants": 2})
    assert characters.run_pending_tasks(settings) == 1
    assert client.get(f"/api/characters/{char_id}").json()["files"]["variants"] == ["variants/1.png", "variants/2.png"]
    assert not (settings.characters_dir / char_id / "variants.new").exists()


def test_prompt_lines(env):
    settings, client = env
    _create(client)
    assert characters.prompt_lines(settings, {}) == []
    lines = characters.prompt_lines(settings.factory, {"characters": "kot_barsik, ghost, ../etc"})
    text = "\n".join(lines)
    assert "addons/character-library/README.md" in lines[0]
    assert str((settings.characters_dir / "kot_barsik" / "character.json").resolve()) in text
    assert "ghost: not found" in text and "etc" not in text
    assert characters.OPTION_KEY == "characters"
