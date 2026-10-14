"""Character library: reusable cartoon characters (sheets + verbatim prompt strings) across films.

Disk is the source of truth: <characters_dir>/<char_id>/character.json holds one
`characters[]` entry of schemas/artifacts/character_bible.schema.json plus a
`library` block (style_lock, tags, source, archived, timestamps). Images sit next
to it: turnaround.<ext>, expression.<ext>, refs/, variants/. The sqlite index in
studio.db only serves listing/search and is rebuilt from disk when missing.

Background generation (Flow turnaround sheets) goes through the `tasks` table;
the factory worker drains it between briefs with run_pending_tasks().
The cartoon-ad agent consumes library characters via addons/character-library.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from studio import auth, projects

OPTION_KEY = "characters"  # brief option (Telegram `characters: a,b` and Studio form)
ROLES = ("hero", "sidekick", "antagonist", "extra", "mascot")
IMAGE_KINDS = ("turnaround", "expression", "ref")
MAX_UPLOAD = 15 * 1024 * 1024
STALE_TASK = timedelta(hours=2)
DEFAULT_STYLE = ("3D Pixar-like animated film style, soft global illumination, rich saturated colors, "
                 "expressive stylized proportions, clean readable silhouettes")
README = "addons/character-library/README.md"
WORKER_TASKS = ("character_generate",)  # Flow work, drained by the factory worker between briefs
MAX_SHEET_REFS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    archived INTEGER NOT NULL DEFAULT 0,
    search TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    args TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sniff(data: bytes) -> Optional[str]:
    """Image extension from magic bytes; the client's filename/content-type are not trusted."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_TRANSLIT = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "abvgdeejziiklmnoprstufhccss_y_eua")


def slugify(text: str, fallback: str = "char") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower().translate(_TRANSLIT)).strip("_")[:40].strip("_")
    return slug or fallback


def normalize_ids(value: Any) -> list[str]:
    """`"a, b"` or `["a","b"]` -> valid unique ids (invalid ones dropped)."""
    items = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    out: list[str] = []
    for item in items:
        item = str(item).strip()
        if item and projects.ID_RE.match(item) and ".." not in item and item not in out:
            out.append(item)
    return out


def _paths(settings: Any) -> tuple[Path, Path]:
    """(characters_dir, studio db) from StudioSettings or the factory's Settings."""
    if hasattr(settings, "characters_dir"):
        return settings.characters_dir, settings.db_path
    return settings.data_dir / "characters", settings.data_dir / "studio.db"


# -- library ---------------------------------------------------------------------
class Library:
    def __init__(self, root: Path, db_path: Path):
        self.root = Path(root)
        self.db_path = Path(db_path)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            missing = c.execute("SELECT 1 FROM sqlite_master WHERE name='characters'").fetchone() is None
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            empty = c.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0
        if missing or (empty and any(self.root.glob("*/character.json"))):
            self.reindex()

    @classmethod
    def of(cls, settings: Any) -> "Library":
        return cls(*_paths(settings))

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # files
    def dir(self, char_id: str) -> Path:
        return self.root / projects.safe_id(char_id)

    def exists(self, char_id: str) -> bool:
        return (self.dir(char_id) / "character.json").is_file()

    def load(self, char_id: str) -> dict[str, Any]:
        path = self.dir(char_id) / "character.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="unknown character")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, char: dict[str, Any]) -> dict[str, Any]:
        char.setdefault("library", {})["updated_at"] = _now()
        d = self.dir(char["id"])
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "character.json.tmp"
        tmp.write_text(json.dumps(char, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(d / "character.json")
        self._index(char)
        return char

    def free_id(self, base: str) -> str:
        base = slugify(base)
        cand, n = base, 2
        while self.dir(cand).exists():
            cand, n = f"{base}_{n}", n + 1
        return cand

    # index
    def _index(self, char: dict[str, Any]) -> None:
        lib = char.get("library") or {}
        tags = [str(t) for t in lib.get("tags") or []]
        search = " ".join([char["id"], char.get("name", ""), char.get("role", ""), char.get("description", ""),
                           char.get("prompt_tokens", ""), *tags]).lower()
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO characters (id, name, role, tags, archived, search, updated_at) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (char["id"], char.get("name", ""), char.get("role", ""), json.dumps(tags, ensure_ascii=False),
                       int(bool(lib.get("archived"))), search, lib.get("updated_at") or _now()))

    def reindex(self) -> int:
        with self._conn() as c:
            c.execute("DELETE FROM characters")
        n = 0
        for path in sorted(self.root.glob("*/character.json")):
            try:
                char = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if char.get("id") == path.parent.name:
                self._index(char)
                n += 1
        return n

    def search(self, q: str = "", tag: str = "", archived: str = "0", world: str = "") -> list[dict[str, Any]]:
        sql, args = "SELECT id FROM characters WHERE 1=1", []
        if archived in ("0", "1"):
            sql += " AND archived = ?"
            args.append(int(archived))
        if q.strip():
            sql += " AND search LIKE ?"
            args.append(f"%{q.strip().lower()}%")
        sql += " ORDER BY updated_at DESC"
        with self._conn() as c:
            ids = [r["id"] for r in c.execute(sql, args)]
        rows = []
        for char_id in ids:
            try:
                char = self.load(char_id)
            except HTTPException:
                continue  # removed on disk; the next reindex drops it
            if tag and tag not in ((char.get("library") or {}).get("tags") or []):
                continue
            if world and (char.get("library") or {}).get("world_id") != world:
                continue
            rows.append(char)
        return rows

    # tasks
    def add_task(self, kind: str, args: dict[str, Any], status: str = "queued") -> str:
        """`status="running"` for tasks Studio executes itself (Claude calls), so the
        factory worker never claims them."""
        task_id = f"t{datetime.now(timezone.utc):%Y%m%d%H%M%S}{secrets.token_hex(3)}"
        now = _now()
        with self._conn() as c:
            c.execute("INSERT INTO tasks (id, kind, args, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                      (task_id, kind, json.dumps(args, ensure_ascii=False), status, now, now))
        return task_id

    def task(self, task_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["args"] = json.loads(out["args"] or "{}")
        out["result"] = json.loads(out["result"]) if out["result"] else None
        return out

    def claim_task(self, kinds: tuple[str, ...] = WORKER_TASKS) -> Optional[dict[str, Any]]:
        marks = ",".join("?" * len(kinds))
        with self._conn() as c:
            for row in c.execute(f"SELECT id FROM tasks WHERE status='queued' AND kind IN ({marks}) "
                                 "ORDER BY created_at, id", kinds).fetchall():
                cur = c.execute("UPDATE tasks SET status='running', updated_at=? WHERE id=? AND status='queued'",
                                (_now(), row["id"]))
                if cur.rowcount:
                    return self.task(row["id"])
        return None

    def finish_task(self, task_id: str, status: str, result: Optional[dict] = None, error: str = "") -> None:
        with self._conn() as c:
            c.execute("UPDATE tasks SET status=?, result=?, error=?, updated_at=? WHERE id=?",
                      (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
                       error or None, _now(), task_id))

    def fail_stale(self) -> None:
        """A worker that died mid-task leaves it `running` forever; give up on old ones."""
        cutoff = (datetime.now(timezone.utc) - STALE_TASK).isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute("UPDATE tasks SET status='failed', error='interrupted', updated_at=? "
                      "WHERE status='running' AND updated_at < ?", (_now(), cutoff))


# -- view helpers ------------------------------------------------------------------
def _images(d: Path, sub: str) -> list[str]:
    folder = d / sub
    if not folder.is_dir():
        return []
    return [f"{sub}/{p.name}" for p in sorted(folder.iterdir(), key=lambda p: (len(p.stem), p.name))
            if p.suffix.lower() in IMAGE_EXTS and p.is_file()]


def _view(lib: Library, char: dict[str, Any], full: bool = True) -> dict[str, Any]:
    out = dict(char)
    lib_meta = dict(char.get("library") or {})
    task = lib.task(lib_meta["task_id"]) if lib_meta.get("task_id") else None
    out["task"] = {k: task[k] for k in ("id", "status", "error", "updated_at")} if task else None
    if full:
        d = lib.dir(char["id"])
        out["files"] = {"refs": _images(d, "refs"), "variants": _images(d, "variants")}
    return out


def _new_char(char_id: str, name: str, role: str, description: str, prompt_tokens: str,
              voice: dict[str, str], style_lock: str, tags: list[str], source: str,
              world_id: str = "") -> dict[str, Any]:
    now = _now()
    char = {
        "id": char_id, "name": name, "role": role, "description": description,
        "turnaround_path": "", "voice": {"language": voice.get("language") or "ru",
                                         "description": voice.get("description") or ""},
        "prompt_tokens": prompt_tokens,
        "library": {"style_lock": style_lock, "tags": tags, "created_at": now, "updated_at": now,
                    "source": source, "archived": False},
    }
    if world_id:
        char["library"]["world_id"] = world_id
    return char


def _clean_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        t = t.strip().lstrip("#")[:40]
        if t and t not in out:
            out.append(t)
    return out[:20]


def _store_image(d: Path, kind: str, data: bytes, ext: str, filename: str = "") -> str:
    """Write an image into the char dir; returns its path relative to the dir."""
    if kind in ("turnaround", "expression"):
        for old in d.glob(f"{kind}.*"):
            old.unlink()
        rel = f"{kind}{ext}"
    else:
        (d / "refs").mkdir(parents=True, exist_ok=True)
        stem = slugify(Path(filename.replace("\\", "/")).stem, "ref")[:32]
        rel = f"refs/{stem}-{secrets.token_hex(3)}{ext}"
    (d / rel).write_bytes(data)
    return rel


def _set_sheet(char: dict[str, Any], kind: str, rel: str) -> None:
    if kind == "turnaround":
        char["turnaround_path"] = rel
    elif kind == "expression":
        char["expression_sheet_path"] = rel


# -- generation ------------------------------------------------------------------
def sheet_prompt(name: str, description: str, style_lock: str, with_refs: bool = False) -> str:
    refs = (" Use the reference images only for this character's identity and the drawing style; "
            "do not copy their composition, pose, background or other characters." if with_refs else "")
    return (f"{style_lock.strip().rstrip('.')}. Character turnaround sheet of {name}: {description.strip().rstrip('.')}. "
            "Three full-body views of the same character side by side: front view, three-quarter view, "
            "side profile view. Neutral standing pose, identical proportions, colors and outfit in every view. "
            "Plain light grey background, even soft studio lighting. "
            f"No text, no labels, no captions, no watermark.{refs}")


def sheet_refs(lib: "Library", char: dict[str, Any]) -> list[str]:
    """Reference images for a sheet: the character's own refs first, then its world's
    family/cover and style refs (studio.worlds), at most MAX_SHEET_REFS."""
    d = lib.dir(char["id"])
    out = [str(d / rel) for rel in _images(d, "refs")][:2]
    world_id = (char.get("library") or {}).get("world_id")
    if world_id:
        from studio import worlds

        # A group picture confuses the model when only one member is wanted (Flow answers
        # "couldn't process the reference"); with the character's own refs use style refs only.
        out += worlds.reference_images(lib.root.parent / "worlds", world_id, style_only=bool(out))
    seen: list[str] = []
    digests: set[str] = set()
    for path in out:
        p = Path(path)
        if path in seen or not p.is_file():
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        if digest not in digests:  # a world's cover is often the same file as its family ref
            digests.add(digest)
            seen.append(path)
    return seen[:MAX_SHEET_REFS]


def _flow_image():
    from tools.graphics.flow_image import FlowImage

    return FlowImage()


def _collect_variants(vdir: Path, output_path: Path, result: Any) -> list[str]:
    """Move whatever the tool produced into <vdir>/1..n.<ext> (output_path and _2,_3 siblings first).
    Returned names are relative to the character dir as they will be after the swap: variants/<n>.<ext>."""
    found: list[Path] = []
    for p in [output_path, *(output_path.with_name(f"{output_path.stem}_{i}{output_path.suffix}") for i in range(2, 5))]:
        if p.is_file():
            found.append(p.resolve())
    data = getattr(result, "data", None) or {}
    for p in [*(getattr(result, "artifacts", None) or []), *(data.get("outputs") or [])]:
        p = Path(p).resolve()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS and p not in found:
            found.append(p)
    vdir.mkdir(parents=True, exist_ok=True)
    staged = []
    for i, src in enumerate(found[:4]):  # stage first: a source may already sit at a final name
        tmp = vdir / f".stage{i}{src.suffix.lower()}"
        if src.is_relative_to(vdir.resolve()):
            src.replace(tmp)
        else:
            shutil.copy2(src, tmp)
        staged.append(tmp)
    names = []
    for i, tmp in enumerate(staged, start=1):
        ext = tmp.suffix if tmp.suffix != ".jpeg" else ".jpg"
        final = vdir / f"{i}{ext}"
        tmp.replace(final)
        names.append(f"variants/{final.name}")
    return names


def _run_generate(lib: Library, task: dict[str, Any]) -> dict[str, Any]:
    args = task["args"]
    char = lib.load(args["character_id"])
    d = lib.dir(char["id"])
    style = (char.get("library") or {}).get("style_lock") or DEFAULT_STYLE
    # generate into variants.new/ and swap it in only on success, so a failed
    # regeneration keeps the previous (paid) variants available for choose-variant
    new_dir = d / "variants.new"
    shutil.rmtree(new_dir, ignore_errors=True)
    new_dir.mkdir(parents=True, exist_ok=True)
    output_path = new_dir / "1.png"
    refs = sheet_refs(lib, char)
    try:
        result = _flow_image().execute({
            "prompt": sheet_prompt(char["name"], char["description"], style, with_refs=bool(refs)),
            "image_paths": refs,
            "model": "nano-pro", "aspect_ratio": "16:9",
            "number_of_images": max(1, min(4, int(args.get("variants") or 1))),
            "output_path": str(output_path), "project_dir": str(d), "scene_id": f"char_{char['id']}",
        })
        if not getattr(result, "success", False):
            raise RuntimeError(getattr(result, "error", None) or "Flow generation failed")
        variants = _collect_variants(new_dir, output_path, result)
        if not variants:
            raise RuntimeError("Flow returned no images")
        for p in new_dir.iterdir():  # drop leftovers (e.g. unused _5 siblings) before the swap
            if not (p.is_file() and f"variants/{p.name}" in variants):
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    except BaseException:
        shutil.rmtree(new_dir, ignore_errors=True)
        raise
    shutil.rmtree(d / "variants", ignore_errors=True)  # a successful regeneration replaces the old set
    new_dir.replace(d / "variants")
    media_ids = list((getattr(result, "data", None) or {}).get("media_ids") or [])
    return {"variants": variants, "media_ids": media_ids[:len(variants)]}


def run_pending_tasks(settings: Any, limit: int = 10) -> int:
    """Execute queued tasks one at a time; returns how many ran. Safe between briefs:
    FlowImage takes the Flow profile lock itself, and failures never raise."""
    lib = Library.of(settings)
    lib.fail_stale()
    done = 0
    while done < limit:
        task = lib.claim_task()
        if task is None:
            break
        done += 1
        try:
            if task["kind"] != "character_generate":
                raise RuntimeError(f"unknown task kind {task['kind']}")
            result = _run_generate(lib, task)
        except Exception as exc:  # noqa: BLE001 — a task failure must not kill the worker
            lib.finish_task(task["id"], "failed", error=str(exc)[:500])
        else:
            lib.finish_task(task["id"], "done", result=result)
    return done


# -- brief prompt hook -----------------------------------------------------------------
def prompt_lines(settings: Any, options: dict[str, Any]) -> list[str]:
    """Lines for factory.runner.build_prompt when the brief picks library characters."""
    ids = normalize_ids((options or {}).get(OPTION_KEY))
    if not ids:
        return []
    root, _ = _paths(settings)
    lines = [f"- Library characters: in the character_design stage apply {README} for these "
             "characters (reuse their sheets and verbatim strings, do not regenerate them):"]
    for char_id in ids:
        path = (root / char_id / "character.json").resolve()
        if path.is_file():
            lines.append(f"  - {char_id}: {path}")
        else:
            lines.append(f"  - {char_id}: not found in the library — ignore it and design the cast as usual")
    return lines


# -- from project ---------------------------------------------------------------------
def _project_bible(project: Path) -> Optional[dict[str, Any]]:
    for base in (project, project / project.name):
        path = base / "artifacts" / "character_bible.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    for base in (project, project / project.name):
        path = base / "checkpoint_character_design.json"
        if path.is_file():
            bible = (json.loads(path.read_text(encoding="utf-8")).get("artifacts") or {}).get("character_bible")
            if isinstance(bible, dict):
                return bible
    return None


def _project_image(project: Path, rel: str) -> Optional[Path]:
    if not rel:
        return None
    candidates = [Path(rel)] if Path(rel).is_absolute() else [project / rel, project / project.name / rel]
    root = project.resolve()
    for cand in candidates:
        target = cand.resolve()
        if target.is_relative_to(root) and target.is_file() and target.suffix.lower() in IMAGE_EXTS:
            return target
    return None


# -- API ------------------------------------------------------------------------------
class VoiceIn(BaseModel):
    language: str = Field(default="ru", max_length=20)
    description: str = Field(default="", max_length=600)


class CharacterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(default="hero", max_length=40)
    description: str = Field(min_length=10, max_length=2000)
    prompt_tokens: str = Field(default="", max_length=400)
    voice: VoiceIn = VoiceIn()
    style_lock: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    id: Optional[str] = Field(default=None, max_length=60)
    world_id: Optional[str] = Field(default=None, max_length=60)


class CharacterPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    role: Optional[str] = Field(default=None, max_length=40)
    description: Optional[str] = Field(default=None, max_length=2000)
    prompt_tokens: Optional[str] = Field(default=None, max_length=400)
    voice: Optional[VoiceIn] = None
    style_lock: Optional[str] = Field(default=None, max_length=1000)
    tags: Optional[list[str]] = Field(default=None, max_length=20)
    archived: Optional[bool] = None
    world_id: Optional[str] = Field(default=None, max_length=60)  # "" detaches


class GenerateIn(CharacterIn):
    variants: int = Field(default=2, ge=1, le=4)


class ChooseIn(BaseModel):
    variant: int = Field(ge=1, le=4)


class FromProjectIn(BaseModel):
    brief_id: Optional[str] = None
    project_id: Optional[str] = None
    character_id: str = Field(min_length=1, max_length=128)


router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])


def _lib(request: Request) -> Library:
    lib = getattr(request.app.state, "characters_lib", None)
    if lib is None:
        lib = request.app.state.characters_lib = Library.of(request.app.state.settings)
    return lib


def _world_style(lib: Library, world_id: str) -> str:
    """Validated world id -> its style_lock (404 for an unknown world)."""
    from studio import worlds

    return worlds.WorldStore(lib.root.parent / "worlds", lib.db_path).load(world_id).get("style_lock") or ""


def _create(lib: Library, body: CharacterIn, source: str) -> dict[str, Any]:
    world_id = projects.safe_id(body.world_id) if body.world_id else ""
    style = body.style_lock.strip() or (_world_style(lib, world_id) if world_id else "")
    char_id = lib.free_id(body.id or body.name)
    char = _new_char(char_id, body.name.strip(), body.role.strip() or "hero", body.description.strip(),
                     body.prompt_tokens.strip() or f"{body.name.strip()}, {body.description.strip()}"[:400],
                     body.voice.model_dump(), style, _clean_tags(body.tags), source, world_id)
    return lib.save(char)


@router.get("/characters")
def list_characters(request: Request, q: str = "", tag: str = "", archived: str = "0", world: str = "") -> list[dict]:
    lib = _lib(request)
    return [_view(lib, c, full=False) for c in lib.search(q, tag, archived, world)]


@router.get("/characters/{char_id}")
def get_character(char_id: str, request: Request) -> dict:
    lib = _lib(request)
    return _view(lib, lib.load(char_id))


@router.post("/characters", status_code=201)
def create_character(body: CharacterIn, request: Request) -> dict:
    lib = _lib(request)
    return _view(lib, _create(lib, body, "manual"))


@router.patch("/characters/{char_id}")
def patch_character(char_id: str, body: CharacterPatch, request: Request) -> dict:
    lib = _lib(request)
    char = lib.load(char_id)
    meta = char.setdefault("library", {})
    for key in ("name", "role", "description", "prompt_tokens"):
        value = getattr(body, key)
        if value is not None:
            char[key] = value.strip()
    if body.voice is not None:
        char["voice"] = body.voice.model_dump()
    if body.style_lock is not None:
        meta["style_lock"] = body.style_lock.strip()
    if body.tags is not None:
        meta["tags"] = _clean_tags(body.tags)
    if body.archived is not None:
        meta["archived"] = body.archived
    if body.world_id is not None:
        if body.world_id:
            _world_style(lib, projects.safe_id(body.world_id))
            meta["world_id"] = projects.safe_id(body.world_id)
        else:
            meta.pop("world_id", None)
    return _view(lib, lib.save(char))


@router.delete("/characters/{char_id}")
def archive_character(char_id: str, request: Request) -> dict:
    lib = _lib(request)
    char = lib.load(char_id)
    char.setdefault("library", {})["archived"] = True
    lib.save(char)
    return {"ok": True, "archived": True}


async def _read_upload(request: Request, kind: Optional[str]) -> tuple[str, bytes, str]:
    """(kind, bytes, filename) from multipart/form-data (`kind`, `file`) or a raw image body
    (`?kind=&filename=`). Parsed with the stdlib: python-multipart is not a dependency."""
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_UPLOAD + 64 * 1024:
            raise HTTPException(status_code=413, detail="файл больше 15 МБ")
    ctype = request.headers.get("content-type", "")
    filename = request.query_params.get("filename", "")
    if ctype.startswith("multipart/form-data"):
        from email import policy
        from email.parser import BytesParser

        msg = BytesParser(policy=policy.HTTP).parsebytes(
            b"Content-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + bytes(body))
        data = b""
        for part in msg.iter_parts() if msg.is_multipart() else []:
            field = part.get_param("name", header="content-disposition")
            if field == "kind":
                kind = (part.get_payload(decode=True) or b"").decode(errors="replace").strip()
            elif field == "file":
                data = part.get_payload(decode=True) or b""
                filename = part.get_filename() or filename
        body = bytearray(data)
    if kind not in IMAGE_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {list(IMAGE_KINDS)}")
    if not body:
        raise HTTPException(status_code=422, detail="пустой файл")
    if len(body) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="файл больше 15 МБ")
    return kind, bytes(body), filename


@router.post("/characters/{char_id}/image")
async def upload_image(char_id: str, request: Request, kind: Optional[str] = None) -> dict:
    lib = _lib(request)
    char = lib.load(char_id)
    kind, data, filename = await _read_upload(request, kind)
    ext = _sniff(data)
    if ext is None:
        raise HTTPException(status_code=415, detail="только PNG, JPG или WebP")
    rel = _store_image(lib.dir(char_id), kind, data, ext, filename)
    _set_sheet(char, kind, rel)
    if kind == "turnaround":
        char.pop("flow_media_ids", None)  # ids pointed at the old sheet
    lib.save(char)
    return {"path": rel, "character": _view(lib, char)}


@router.get("/characters/{char_id}/file/{name:path}")
async def character_file(char_id: str, name: str, request: Request, w: Optional[int] = None) -> FileResponse:
    lib = _lib(request)
    base = lib.dir(char_id).resolve()
    target = (base / name).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(status_code=403, detail="path escapes character")
    if not target.is_file() or target.suffix.lower() not in IMAGE_EXTS:
        raise HTTPException(status_code=404, detail="file not found")
    if w:
        import asyncio

        from backlot.server import THUMB_WIDTHS, _thumbnail_for

        width = min(THUMB_WIDTHS, key=lambda x: abs(x - w))
        cached = await asyncio.to_thread(_thumbnail_for, target, width)
        if cached is not None:
            return FileResponse(cached, media_type="image/jpeg")
    return FileResponse(target)


@router.post("/characters/generate", status_code=201)
def generate_character(body: GenerateIn, request: Request) -> dict:
    lib = _lib(request)
    if not body.style_lock.strip() and not body.world_id:
        body.style_lock = DEFAULT_STYLE
    char = _create(lib, body, "generated")
    if not char["library"].get("style_lock"):
        char["library"]["style_lock"] = DEFAULT_STYLE
    task_id = lib.add_task("character_generate", {"character_id": char["id"], "variants": body.variants})
    char["library"]["task_id"] = task_id
    lib.save(char)
    return {"id": char["id"], "task_id": task_id, "character": _view(lib, char)}


class RegenerateIn(BaseModel):
    variants: int = Field(default=2, ge=1, le=4)


@router.post("/characters/{char_id}/generate", status_code=201)
def regenerate_character(char_id: str, body: RegenerateIn, request: Request) -> dict:
    """Queue a new sheet generation for an existing character (e.g. after a failed task)."""
    lib = _lib(request)
    char = lib.load(char_id)
    meta = char.setdefault("library", {})
    old = lib.task(meta["task_id"]) if meta.get("task_id") else None
    if old and old["status"] in ("queued", "running"):
        raise HTTPException(status_code=409, detail="генерация уже идёт")
    task_id = lib.add_task("character_generate", {"character_id": char_id, "variants": body.variants})
    meta["task_id"] = task_id
    lib.save(char)
    return {"id": char_id, "task_id": task_id, "character": _view(lib, char)}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict:
    task = _lib(request).task(projects.safe_id(task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="unknown task")
    return task


@router.post("/characters/{char_id}/choose-variant")
def choose_variant(char_id: str, body: ChooseIn, request: Request) -> dict:
    lib = _lib(request)
    char = lib.load(char_id)
    d = lib.dir(char_id)
    src = next((p for p in sorted((d / "variants").glob(f"{body.variant}.*")) if p.suffix.lower() in IMAGE_EXTS), None)
    if src is None:
        raise HTTPException(status_code=404, detail="no such variant")
    rel = _store_image(d, "turnaround", src.read_bytes(), src.suffix.lower())
    _set_sheet(char, "turnaround", rel)
    task = lib.task(char["library"]["task_id"]) if char.get("library", {}).get("task_id") else None
    media = ((task or {}).get("result") or {}).get("media_ids") or []
    if len(media) >= body.variant and media[body.variant - 1]:
        char["flow_media_ids"] = [media[body.variant - 1]]
    else:
        char.pop("flow_media_ids", None)
    return _view(lib, lib.save(char))


@router.post("/characters/from-project", status_code=201)
def from_project(body: FromProjectIn, request: Request) -> dict:
    settings = request.app.state.settings
    lib = _lib(request)
    project: Optional[Path] = None
    if body.brief_id:
        from factory.db import BriefStore

        brief = BriefStore(settings.factory.db_path).get(projects.safe_id(body.brief_id))
        if brief and brief.get("project_dir") and Path(brief["project_dir"]).is_dir():
            project = Path(brief["project_dir"])
        else:
            project = projects.project_dir(settings.factory.projects_dir, body.brief_id)
        src_id = body.brief_id
    elif body.project_id:
        project = projects.project_dir(settings.factory.projects_dir, body.project_id)
        src_id = body.project_id
    else:
        raise HTTPException(status_code=422, detail="brief_id or project_id required")
    if project is None:
        raise HTTPException(status_code=404, detail="no project")
    bible = _project_bible(project)
    if bible is None:
        raise HTTPException(status_code=404, detail="no character_bible in project")
    src = next((c for c in bible.get("characters") or [] if c.get("id") == body.character_id), None)
    if src is None:
        raise HTTPException(status_code=404, detail="character not in character_bible")

    char_id = lib.free_id(src["id"])
    voice = src.get("voice") or {}
    char = _new_char(char_id, src.get("name") or char_id, src.get("role") or "", src.get("description") or "",
                     src.get("prompt_tokens") or "", voice, bible.get("style_lock") or "", [],
                     f"project:{src_id}")
    d = lib.dir(char_id)
    d.mkdir(parents=True, exist_ok=True)
    for kind, key in (("turnaround", "turnaround_path"), ("expression", "expression_sheet_path")):
        image = _project_image(project, src.get(key) or "")
        if image is not None:
            _set_sheet(char, kind, _store_image(d, kind, image.read_bytes(),
                                                ".jpg" if image.suffix.lower() == ".jpeg" else image.suffix.lower()))
    if src.get("flow_media_ids"):
        char["flow_media_ids"] = list(src["flow_media_ids"])
    return _view(lib, lib.save(char))


__all__ = ["OPTION_KEY", "WORKER_TASKS", "Library", "sheet_refs", "router", "run_pending_tasks", "prompt_lines", "normalize_ids", "sheet_prompt"]
