"""Worlds: a series' look and cast — style lock, reference images, setting, episode rules and
the library characters that live in it (character.library.world_id).

Disk is the source of truth: <worlds_dir>/<world_id>/world.json plus refs/ (cover, style,
location and family images). studio.db keeps a `worlds` index for listing.

Text work (turning an archive prompt into a structured world + cast, planning a week of
episodes) is done by headless Claude (`claude -p`) in a Studio background thread; those
tasks are created `running` in the shared `tasks` table so the factory worker (which only
claims Flow tasks) never picks them up. Episode runs read the world through
addons/series/README.md (factory.runner -> prompt_lines).
"""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import sqlite3
import subprocess
import threading
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from studio import auth, characters, projects

OPTION_KEY = "world"
README = "addons/series/README.md"
REF_KINDS = ("cover", "style", "location", "family")
MAX_ARCHIVE = 200 * 1024 * 1024
MAX_ARCHIVE_FILE = 25 * 1024 * 1024
CLAUDE_TIMEOUT_S = 900

SCHEMA = """
CREATE TABLE IF NOT EXISTS worlds (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""

FIELDS = ("name", "label", "logline", "audience", "setting", "style_lock", "negative_style",
          "episode_rules", "duration")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def worlds_dir(settings: Any) -> Path:
    if hasattr(settings, "worlds_dir"):
        return settings.worlds_dir
    data = settings.factory.data_dir if hasattr(settings, "factory") else settings.data_dir
    return data / "worlds"


def _db(settings: Any) -> Path:
    return settings.db_path if hasattr(settings, "factory") else settings.data_dir / "studio.db"


# -- store ---------------------------------------------------------------------------
class WorldStore:
    def __init__(self, root: Path, db_path: Path):
        self.root = Path(root)
        self.db_path = Path(db_path)
        self.root.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            empty = c.execute("SELECT COUNT(*) FROM worlds").fetchone()[0] == 0
        if empty and any(self.root.glob("*/world.json")):
            self.reindex()

    @classmethod
    def of(cls, settings: Any) -> "WorldStore":
        return cls(worlds_dir(settings), _db(settings))

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def dir(self, world_id: str) -> Path:
        return self.root / projects.safe_id(world_id)

    def exists(self, world_id: str) -> bool:
        return (self.dir(world_id) / "world.json").is_file()

    def load(self, world_id: str) -> dict[str, Any]:
        path = self.dir(world_id) / "world.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="unknown world")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, world: dict[str, Any]) -> dict[str, Any]:
        world["updated_at"] = _now()
        d = self.dir(world["id"])
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "world.json.tmp"
        tmp.write_text(json.dumps(world, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(d / "world.json")
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO worlds (id, name, archived, updated_at) VALUES (?,?,?,?)",
                      (world["id"], world.get("name", ""), int(bool(world.get("archived"))), world["updated_at"]))
        return world

    def free_id(self, base: str) -> str:
        base = characters.slugify(base, "world")
        cand, n = base, 2
        while self.dir(cand).exists():
            cand, n = f"{base}_{n}", n + 1
        return cand

    def reindex(self) -> int:
        with self._conn() as c:
            c.execute("DELETE FROM worlds")
        n = 0
        for path in sorted(self.root.glob("*/world.json")):
            try:
                world = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if world.get("id") == path.parent.name:
                with self._conn() as c:
                    c.execute("INSERT OR REPLACE INTO worlds (id, name, archived, updated_at) VALUES (?,?,?,?)",
                              (world["id"], world.get("name", ""), int(bool(world.get("archived"))),
                               world.get("updated_at") or _now()))
                n += 1
        return n

    def list(self, archived: str = "0") -> list[dict[str, Any]]:
        sql, args = "SELECT id FROM worlds", []
        if archived in ("0", "1"):
            sql += " WHERE archived = ?"
            args.append(int(archived))
        with self._conn() as c:
            ids = [r["id"] for r in c.execute(sql + " ORDER BY name", args)]
        out = []
        for world_id in ids:
            try:
                out.append(self.load(world_id))
            except HTTPException:
                continue
        return out


def new_world(world_id: str, name: str, **fields: Any) -> dict[str, Any]:
    now = _now()
    world: dict[str, Any] = {"id": world_id, "name": name, "label": "", "logline": "", "audience": "",
                             "setting": "", "style_lock": "", "negative_style": "", "episode_rules": "",
                             "duration": "30-45", "cover": "", "refs": [], "source_prompt": "",
                             "draft": None, "task_id": None, "archived": False,
                             "created_at": now, "updated_at": now}
    world.update({k: v for k, v in fields.items() if v is not None})
    return world


def store_ref(d: Path, kind: str, data: bytes, ext: str, filename: str = "") -> str:
    (d / "refs").mkdir(parents=True, exist_ok=True)
    stem = characters.slugify(Path(filename.replace("\\", "/")).stem, kind)[:32]
    rel = f"refs/{kind}-{stem}-{secrets.token_hex(3)}{ext}"
    (d / rel).write_bytes(data)
    return rel


def add_ref(store: WorldStore, world: dict[str, Any], kind: str, data: bytes, filename: str = "") -> str:
    ext = characters._sniff(data)
    if ext is None:
        raise HTTPException(status_code=415, detail="только PNG, JPG или WebP")
    rel = store_ref(store.dir(world["id"]), kind, data, ext, filename)
    if kind == "cover":
        world["cover"] = rel
    else:
        world.setdefault("refs", []).append({"kind": kind, "path": rel, "name": Path(filename).name[:120]})
    return rel


def reference_images(root: Path, world_id: str, style_only: bool = False) -> list[str]:
    """Absolute paths a character sheet of this world should be drawn against:
    family refs, the cover, then style refs (only style refs with `style_only`)."""
    path = Path(root) / projects.safe_id(world_id) / "world.json"
    if not path.is_file():
        return []
    world = json.loads(path.read_text(encoding="utf-8"))
    d = path.parent
    refs = world.get("refs") or []
    rels = [] if style_only else [r["path"] for r in refs if r.get("kind") == "family"]
    rels += [world["cover"]] if world.get("cover") and not style_only else []
    rels += [r["path"] for r in refs if r.get("kind") == "style"]
    out: list[str] = []
    for rel in rels:
        target = (d / rel).resolve()
        if target.is_relative_to(d.resolve()) and target.is_file() and str(target) not in out:
            out.append(str(target))
    return out


def world_characters(settings: Any, world_id: str, with_sheet: bool = False) -> list[dict[str, Any]]:
    lib = characters.Library.of(settings)
    chars = lib.search(world=world_id)
    return [c for c in chars if c.get("turnaround_path")] if with_sheet else chars


# -- archive import ------------------------------------------------------------------------
def _label_name(label: str) -> str:
    text = label.split("·", 1)[-1].strip() or label.strip()
    return text[:1].upper() + text[1:].lower() if text.isupper() else text


def import_archive(store: WorldStore, data: bytes) -> list[str]:
    """Worlds from a world-adaptations zip (generation-manifest.json + images). Only the
    manifest and the images it names are read; paths are never extracted to disk as-is."""
    if len(data) > MAX_ARCHIVE:
        raise HTTPException(status_code=413, detail="архив больше 200 МБ")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="это не zip-архив") from None
    members = {i.filename: i for i in zf.infolist() if not i.is_dir()}

    def read(name: str) -> Optional[bytes]:
        info = members.get(name)
        if info is None or info.file_size > MAX_ARCHIVE_FILE or ".." in name.split("/") or name.startswith("/"):
            return None
        return zf.read(info)

    raw = read("generation-manifest.json")
    if raw is None:
        raise HTTPException(status_code=422, detail="в архиве нет generation-manifest.json")
    try:
        manifest = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="generation-manifest.json не читается") from None
    if not isinstance(manifest, list):
        raise HTTPException(status_code=422, detail="generation-manifest.json: ожидается список")
    created: list[str] = []
    for item in manifest[:50]:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        label = str(item.get("label") or item["name"])[:120]
        name = _label_name(label)[:80]
        world = new_world(store.free_id(name), name, label=label, source_prompt=str(item.get("prompt") or "")[:20000])
        image = read(str(item["name"]))
        if image and characters._sniff(image):
            add_ref(store, world, "cover", image, item["name"])
            add_ref(store, world, "family", image, item["name"])
        for ref in item.get("reference_files") or []:
            ref_data = read(str(ref))
            if ref_data and characters._sniff(ref_data):
                add_ref(store, world, "style", ref_data, str(ref))
        store.save(world)
        created.append(world["id"])
    if not created:
        raise HTTPException(status_code=422, detail="в манифесте нет миров")
    return created


# -- headless Claude --------------------------------------------------------------------------
def ask_claude_json(settings: Any, prompt: str, timeout: float = CLAUDE_TIMEOUT_S) -> dict[str, Any]:
    """One `claude -p` call that must answer with a single JSON object (no tools)."""
    from factory.runner import build_env

    fs = settings.factory if hasattr(settings, "factory") else settings
    proc = subprocess.run([fs.claude_bin, "-p", prompt, "--output-format", "text", "--max-turns", "3"],
                          capture_output=True, text=True, timeout=timeout, cwd=str(fs.repo),
                          env=build_env(fs, dict(os.environ)))
    out = proc.stdout or ""
    start, end = out.find("{"), out.rfind("}")
    if proc.returncode != 0 or start < 0 or end <= start:
        raise RuntimeError(f"claude failed ({proc.returncode}): {(proc.stderr or out)[-400:]}")
    return json.loads(out[start:end + 1])


EXTRACT_PROMPT = """You turn an image-generation prompt for an animated family comedy world into a
structured series bible. Answer with ONE JSON object only, no prose, with these keys:
{
  "name": "short Russian world title",
  "logline": "Russian, 1-2 sentences: what the series is about and its kind of humor",
  "audience": "Russian, who it is for",
  "setting": "English, the world and its recurring locations, concrete visual details",
  "style_lock": "English, 40-90 words, the drawing/rendering style ONLY (medium, line, shading, palette, texture). No characters, no film/cinema words, no aspect ratio",
  "negative_style": "English, comma list of what must never appear for THIS style (e.g. for 2D: no 3D render, no glossy CG; for clay: no smooth CG plastic). Also: no text, no watermarks, no extra limbs",
  "episode_rules": "Russian, 3-5 short rules for episodes: format, tone, what stays constant",
  "characters": [
    {"name": "Russian given name you invent", "role": "mother|father|daughter|son|grandma|...",
     "description": "Russian, 1-3 sentences: look and personality",
     "prompt_tokens": "English, <=350 chars, the exact visual identity (species/body, colors, face, clothes, signature props) copied from the source wording",
     "voice": {"language": "ru", "description": "Russian, voice timbre, age, manner"}}
  ]
}
Keep every visual detail of each character from the source; do not invent new looks.
SOURCE PROMPT:
"""

PLAN_PROMPT = """You are the head writer of a short animated Reels series. Plan {days} NEW episodes
(one per day) for this world. Each episode is a 30-45 s vertical comedy sketch with one clear
comic beat, 3-4 shots, short Russian dialogue lines for the characters, and a punchline in the
last shot. Use only the characters listed (by id). Do not repeat ideas from the past episodes.
Answer with ONE JSON object only:
{{"episodes": [{{"title": "Russian", "logline": "Russian, one sentence",
  "synopsis": "Russian, 3-5 numbered beats with the exact dialogue lines in «quotes» and who says them",
  "characters": ["character ids"]}}]}}
WORLD:
{world}
CHARACTERS:
{chars}
PAST EPISODES (avoid repeating):
{past}
OWNER WISHES:
{wishes}
"""


def _clip(text: Any, n: int) -> str:
    return str(text or "").strip()[:n]


def extract_world(settings: Any, store: WorldStore, world_id: str) -> dict[str, Any]:
    world = store.load(world_id)
    if not (world.get("source_prompt") or world.get("setting")):
        raise RuntimeError("нет исходного промпта для разбора")
    answer = ask_claude_json(settings, EXTRACT_PROMPT + (world.get("source_prompt") or json.dumps(world, ensure_ascii=False)))
    draft = {k: _clip(answer.get(k), 2000) for k in ("name", "logline", "audience", "setting", "style_lock",
                                                      "negative_style", "episode_rules")}
    draft["characters"] = []
    for ch in (answer.get("characters") or [])[:8]:
        if not isinstance(ch, dict) or not ch.get("name"):
            continue
        voice = ch.get("voice") if isinstance(ch.get("voice"), dict) else {}
        draft["characters"].append({
            "name": _clip(ch.get("name"), 80), "role": _clip(ch.get("role"), 40) or "hero",
            "description": _clip(ch.get("description"), 2000), "prompt_tokens": _clip(ch.get("prompt_tokens"), 400),
            "voice": {"language": _clip(voice.get("language"), 20) or "ru", "description": _clip(voice.get("description"), 600)}})
    world = store.load(world_id)
    world["draft"] = draft
    store.save(world)
    return draft


def run_in_background(settings: Any, kind: str, args: dict[str, Any], fn: Callable[[], Any]) -> str:
    """Record a `running` task and execute `fn` in a daemon thread (Claude work, not Flow)."""
    lib = characters.Library.of(settings)
    task_id = lib.add_task(kind, args, status="running")

    def work() -> None:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            lib.finish_task(task_id, "failed", error=str(exc)[:500])
        else:
            lib.finish_task(task_id, "done", result=result if isinstance(result, dict) else {"result": result})

    threading.Thread(target=work, name=f"studio-{kind}", daemon=True).start()
    return task_id


# -- brief prompt hook ---------------------------------------------------------------------
def prompt_lines(settings: Any, options: dict[str, Any]) -> list[str]:
    """Lines for factory.runner.build_prompt when the brief is an episode of a world."""
    world_id = (options or {}).get(OPTION_KEY)
    if not world_id or not projects.ID_RE.match(str(world_id)):
        return []
    path = (worlds_dir(settings) / world_id / "world.json").resolve()
    if not path.is_file():
        return [f"- World `{world_id}` not found in Studio — run as a normal brief."]
    if options.get("format") == "mural":
        # addons/mural replaces the series (Veo) rules; the world only supplies style, setting and cast
        lines = [f"- Mural episode of a world (NOT an ad): use the world bible {path} for style_lock, "
                 "negative_style, setting, narrator and cast; do NOT apply addons/series."]
    else:
        lines = [f"- Series episode (NOT an ad): apply {README} with the world bible {path}."]
    if options.get("episode"):
        lines.append(f"- Episode id: {options['episode']} (planned in Studio).")
    if not characters.normalize_ids(options.get(characters.OPTION_KEY)):
        cast = [c["id"] for c in world_characters(settings, world_id, with_sheet=True)]
        if cast:
            lines += characters.prompt_lines(settings, {characters.OPTION_KEY: ",".join(cast)})
    return lines


# -- API ---------------------------------------------------------------------------------
class WorldIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    label: str = Field(default="", max_length=120)
    logline: str = Field(default="", max_length=2000)
    audience: str = Field(default="", max_length=600)
    setting: str = Field(default="", max_length=3000)
    style_lock: str = Field(default="", max_length=1500)
    negative_style: str = Field(default="", max_length=800)
    episode_rules: str = Field(default="", max_length=3000)
    duration: str = Field(default="30-45", max_length=20)


class WorldPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    label: Optional[str] = Field(default=None, max_length=120)
    logline: Optional[str] = Field(default=None, max_length=2000)
    audience: Optional[str] = Field(default=None, max_length=600)
    setting: Optional[str] = Field(default=None, max_length=3000)
    style_lock: Optional[str] = Field(default=None, max_length=1500)
    negative_style: Optional[str] = Field(default=None, max_length=800)
    episode_rules: Optional[str] = Field(default=None, max_length=3000)
    duration: Optional[str] = Field(default=None, max_length=20)
    archived: Optional[bool] = None


class DraftCharacter(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(default="hero", max_length=40)
    description: str = Field(min_length=10, max_length=2000)
    prompt_tokens: str = Field(default="", max_length=400)
    voice: characters.VoiceIn = characters.VoiceIn()
    generate: bool = True


class ApplyDraftIn(WorldPatch):
    characters: list[DraftCharacter] = Field(default_factory=list, max_length=8)
    variants: int = Field(default=2, ge=1, le=4)


router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])


def _store(request: Request) -> WorldStore:
    st = getattr(request.app.state, "worlds_store", None)
    if st is None:
        st = request.app.state.worlds_store = WorldStore.of(request.app.state.settings)
    return st


def _view(request: Request, world: dict[str, Any], full: bool = True) -> dict[str, Any]:
    out = dict(world)
    settings = request.app.state.settings
    lib = characters.Library.of(settings)
    task = lib.task(world["task_id"]) if world.get("task_id") else None
    out["task"] = {k: task[k] for k in ("id", "kind", "status", "error", "updated_at")} if task else None
    chars = world_characters(settings, world["id"])
    out["character_count"] = len(chars)
    if full:
        out["characters"] = [{"id": c["id"], "name": c.get("name"), "role": c.get("role"),
                              "turnaround_path": c.get("turnaround_path") or "",
                              "task": characters._view(lib, c, full=False)["task"]} for c in chars]
    return out


@router.get("/worlds")
def list_worlds(request: Request, archived: str = "0") -> list[dict]:
    return [_view(request, w, full=False) for w in _store(request).list(archived)]


@router.post("/worlds", status_code=201)
def create_world(body: WorldIn, request: Request) -> dict:
    store = _store(request)
    data = {k: v.strip() for k, v in body.model_dump().items()}
    world = new_world(store.free_id(data.pop("name")), body.name.strip(), **data)
    return _view(request, store.save(world))


@router.get("/worlds/{world_id}")
def get_world(world_id: str, request: Request) -> dict:
    return _view(request, _store(request).load(world_id))


def _patch(world: dict[str, Any], body: WorldPatch) -> None:
    for key in FIELDS:
        value = getattr(body, key, None)
        if value is not None:
            world[key] = value.strip()
    if body.archived is not None:
        world["archived"] = body.archived


@router.patch("/worlds/{world_id}")
def patch_world(world_id: str, body: WorldPatch, request: Request) -> dict:
    store = _store(request)
    world = store.load(world_id)
    _patch(world, body)
    return _view(request, store.save(world))


@router.post("/worlds/{world_id}/image")
async def upload_world_image(world_id: str, request: Request, kind: Optional[str] = None) -> dict:
    store = _store(request)
    world = store.load(world_id)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > characters.MAX_UPLOAD + 64 * 1024:
            raise HTTPException(status_code=413, detail="файл больше 15 МБ")
    if kind not in REF_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {list(REF_KINDS)}")
    if not body:
        raise HTTPException(status_code=422, detail="пустой файл")
    rel = add_ref(store, world, kind, bytes(body), request.query_params.get("filename", ""))
    store.save(world)
    return {"path": rel, "world": _view(request, world)}


@router.delete("/worlds/{world_id}/refs/{name:path}")
def delete_world_ref(world_id: str, name: str, request: Request) -> dict:
    store = _store(request)
    world = store.load(world_id)
    before = len(world.get("refs") or [])
    world["refs"] = [r for r in world.get("refs") or [] if r.get("path") != name]
    if len(world["refs"]) == before and world.get("cover") != name:
        raise HTTPException(status_code=404, detail="no such reference")
    if world.get("cover") == name:
        world["cover"] = ""
    target = (store.dir(world_id) / name).resolve()
    if target.is_relative_to(store.dir(world_id).resolve()) and target.is_file():
        target.unlink()
    return _view(request, store.save(world))


@router.get("/worlds/{world_id}/file/{name:path}")
async def world_file(world_id: str, name: str, request: Request, w: Optional[int] = None) -> FileResponse:
    base = _store(request).dir(world_id).resolve()
    target = (base / name).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(status_code=403, detail="path escapes world")
    if not target.is_file() or target.suffix.lower() not in characters.IMAGE_EXTS:
        raise HTTPException(status_code=404, detail="file not found")
    if w:
        import asyncio

        from backlot.server import THUMB_WIDTHS, _thumbnail_for

        width = min(THUMB_WIDTHS, key=lambda x: abs(x - w))
        cached = await asyncio.to_thread(_thumbnail_for, target, width)
        if cached is not None:
            return FileResponse(cached, media_type="image/jpeg")
    return FileResponse(target)


@router.post("/worlds/import", status_code=201)
async def import_worlds(request: Request, extract: int = 1) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_ARCHIVE:
            raise HTTPException(status_code=413, detail="архив больше 200 МБ")
    store = _store(request)
    ids = import_archive(store, bytes(body))
    if extract:
        settings = request.app.state.settings
        task_id = run_in_background(settings, "world_extract", {"worlds": ids},
                                    lambda: _extract_many(settings, store, ids))
        for world_id in ids:
            world = store.load(world_id)
            world["task_id"] = task_id
            store.save(world)
    return {"ids": ids}


def _extract_many(settings: Any, store: WorldStore, ids: list[str]) -> dict[str, Any]:
    errors = {}
    for world_id in ids:  # one at a time: each is a separate Claude call
        try:
            extract_world(settings, store, world_id)
        except Exception as exc:  # noqa: BLE001
            errors[world_id] = str(exc)[:300]
    if errors and len(errors) == len(ids):
        raise RuntimeError("; ".join(f"{k}: {v}" for k, v in errors.items())[:500])
    return {"worlds": ids, "errors": errors}


@router.post("/worlds/{world_id}/extract", status_code=201)
def extract(world_id: str, request: Request) -> dict:
    store = _store(request)
    world = store.load(world_id)
    lib = characters.Library.of(request.app.state.settings)
    old = lib.task(world["task_id"]) if world.get("task_id") else None
    if old and old["status"] in ("queued", "running"):
        raise HTTPException(status_code=409, detail="разбор уже идёт")
    settings = request.app.state.settings
    task_id = run_in_background(settings, "world_extract", {"worlds": [world_id]},
                                lambda: _extract_many(settings, store, [world_id]))
    world["task_id"] = task_id
    store.save(world)
    return {"task_id": task_id}


@router.post("/worlds/{world_id}/apply-draft")
def apply_draft(world_id: str, body: ApplyDraftIn, request: Request) -> dict:
    """Save the (edited) draft into the world and create its characters; queue sheet generation."""
    store = _store(request)
    world = store.load(world_id)
    _patch(world, body)
    world["draft"] = None
    store.save(world)
    lib = characters.Library.of(request.app.state.settings)
    created = []
    for ch in body.characters:
        char = characters._create(lib, characters.CharacterIn(
            name=ch.name, role=ch.role, description=ch.description, prompt_tokens=ch.prompt_tokens,
            voice=ch.voice, style_lock="", tags=[], world_id=world_id), "generated")
        if ch.generate:
            char["library"]["task_id"] = lib.add_task("character_generate",
                                                      {"character_id": char["id"], "variants": body.variants})
            lib.save(char)
        created.append(char["id"])
    return {"world": _view(request, world), "characters": created}


__all__ = ["OPTION_KEY", "WorldStore", "router", "prompt_lines", "reference_images", "import_archive",
           "ask_claude_json", "extract_world", "world_characters", "run_in_background"]
