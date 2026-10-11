"""Studio — web dashboard for the cartoon-ad factory (and, later, the phone farm).

    uvicorn studio.app:app --host 127.0.0.1 --port 4760

All /api routes except /api/auth/login and /api/health need the owner's session.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from factory import actions
from factory.db import BriefStore, InvalidTransition
from factory.service import BAIT_MODES
from factory.tg import Telegram
from studio import auth, projects
from studio import config as studio_config
from studio.config import StudioSettings

SSE_POLL_S = 2.0
SSE_HEARTBEAT_S = 15.0
VIDEO_STATES = ("awaiting_approval", "approved", "rejected")


# -- models -------------------------------------------------------------------
class LoginIn(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


class BriefOptions(BaseModel):
    comment_bait: Optional[str] = None
    product: Optional[str] = Field(default=None, max_length=300)
    duration: Optional[str] = Field(default=None, max_length=40)
    audience: Optional[str] = Field(default=None, max_length=300)
    style: Optional[str] = Field(default=None, max_length=300)


class BriefIn(BaseModel):
    text: str = Field(min_length=10, max_length=4000)
    options: BriefOptions = BriefOptions()


class CaptionIn(BaseModel):
    caption: str = Field(max_length=2200)
    hashtags: list[str] = Field(default_factory=list, max_length=30)


# -- helpers --------------------------------------------------------------------
def _store(request: Request) -> BriefStore:
    return request.app.state.store


def _notify(request: Request):
    tg: Telegram = request.app.state.tg
    return tg.send if tg.enabled else None


def _staged(brief: dict[str, Any]) -> dict[str, Any]:
    detail = brief.get("detail") or ""
    if detail.startswith("{"):
        try:
            return json.loads(detail)
        except ValueError:
            return {}
    return {}


def _project_of(settings: StudioSettings, brief: dict[str, Any]) -> Optional[Path]:
    if brief.get("project_dir"):
        path = Path(brief["project_dir"])
        if path.is_dir():
            return path
    return projects.project_dir(settings.factory.projects_dir, brief["id"])


def _release_dir(settings: StudioSettings, brief_id: str) -> Optional[Path]:
    root = settings.factory.releases_dir
    for path in (root / "pending" / brief_id, root / brief_id):
        if path.is_dir():
            return path
    return None


def _release_meta(release: Optional[Path]) -> dict[str, Any]:
    if release is None:
        return {}
    path = release / "checkpoint_publish.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = ((data.get("artifacts") or {}).get("publish_log") or {}).get("entries") or []
    first = (entries[0].get("metadata_used") or {}) if entries else {}
    return {"caption": first.get("description") or first.get("title") or "",
            "hashtags": first.get("hashtags") or [],
            "platforms": [e.get("platform") for e in entries],
            "files": sorted(p.name for p in release.iterdir() if p.is_file())}


def _brief_row(settings: StudioSettings, brief: dict[str, Any]) -> dict[str, Any]:
    project = _project_of(settings, brief)
    rail = projects.stages(project, active=brief["state"] == "running")
    return {
        "id": brief["id"], "text": brief["text"], "options": brief["options"], "state": brief["state"],
        "detail": None if (brief.get("detail") or "").startswith("{") else brief.get("detail"),
        "created_at": brief["created_at"], "updated_at": brief["updated_at"],
        "stages_done": sum(1 for s in rail if s["status"] == "done"), "stages_total": len(rail),
        "current_stage": next((s["stage"] for s in rail if s["status"] == "current"), None),
        "credits": projects.ledger(project) if project else {},
        "has_project": project is not None,
    }


def _service_states() -> dict[str, str]:
    if not shutil.which("systemctl"):
        return {}
    names = ["factory-worker", "factory-bot", "factory-studio"]
    try:
        out = subprocess.run(["systemctl", "is-active", *names], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return dict(zip(names, out.split()))


def _safe_child(base: Path, name: str) -> Path:
    target = (base / name).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="path escapes release")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return target


async def _thumb(source: Path, width: int) -> FileResponse:
    from backlot.server import THUMB_WIDTHS, _thumbnail_for

    w = min(THUMB_WIDTHS, key=lambda x: abs(x - width))
    cached = await asyncio.to_thread(_thumbnail_for, source, w)
    if cached is None:
        if source.suffix.lower() in {".mp4", ".webm", ".mov"}:
            raise HTTPException(status_code=404, detail="no poster frame")
        return FileResponse(source)
    return FileResponse(cached, media_type="image/jpeg")


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown {exc}")
    except (InvalidTransition, actions.ActionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# -- routers --------------------------------------------------------------------
public = APIRouter(prefix="/api")
api = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])


@public.get("/health")
def health() -> dict:
    return {"ok": True, "app": "studio"}


@public.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    settings: StudioSettings = request.app.state.settings
    limiter: auth.LoginLimiter = request.app.state.limiter
    ip = auth.client_ip(request)
    if limiter.blocked(ip):
        raise HTTPException(status_code=429, detail="слишком много попыток, подождите 15 минут")
    if not settings.admin_password_hash or not settings.secret:
        raise HTTPException(status_code=503, detail="STUDIO_ADMIN_PASSWORD_HASH / STUDIO_SECRET not set")
    ok = (body.username == settings.admin_user) & auth.verify_password(body.password, settings.admin_password_hash)
    if not ok:
        limiter.fail(ip)
        raise HTTPException(status_code=401, detail="неверный логин или пароль")
    limiter.reset(ip)
    ttl = settings.session_days * 86400
    response.set_cookie(auth.COOKIE, auth.sign_session(settings.secret, settings.admin_user, ttl),
                        max_age=ttl, httponly=True, secure=settings.cookie_secure, samesite="strict", path="/")
    return {"user": settings.admin_user}


@public.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@api.get("/auth/me")
def me(user: str = Depends(auth.require_user)) -> dict:
    return {"user": user}


@api.get("/overview")
def overview(request: Request) -> dict:
    settings: StudioSettings = request.app.state.settings
    store = _store(request)
    running = [_brief_row(settings, b) for b in store.list(("running",), limit=5)]
    awaiting = store.list(("awaiting_approval",), limit=50)
    recent = [_brief_row(settings, b) for b in store.list(limit=8)]
    return {"counts": store.counts(), "running": running, "awaiting_count": len(awaiting),
            "recent": recent, "services": _service_states(), "server_time": time.time()}


@api.get("/briefs")
def list_briefs(request: Request, state: Optional[str] = None, limit: int = 100) -> list[dict]:
    settings: StudioSettings = request.app.state.settings
    states = tuple(s for s in (state or "").split(",") if s) or None
    return [_brief_row(settings, b) for b in _store(request).list(states, limit=min(limit, 500))]


@api.post("/briefs", status_code=201)
def create_brief(body: BriefIn, request: Request) -> dict:
    opts = {k: v.strip() for k, v in body.options.model_dump().items() if isinstance(v, str) and v.strip()}
    if opts.get("comment_bait") not in (None, *BAIT_MODES):
        raise HTTPException(status_code=422, detail=f"comment_bait must be one of {sorted(BAIT_MODES)}")
    brief_id = _store(request).add(body.text.strip(), opts)
    notify = _notify(request)
    if notify:
        with suppress(Exception):
            notify(f"📝 В очереди (из Studio): {brief_id}")
    return {"id": brief_id}


@api.get("/briefs/{brief_id}")
def brief_detail(brief_id: str, request: Request) -> dict:
    settings: StudioSettings = request.app.state.settings
    brief = _store(request).get(projects.safe_id(brief_id))
    if brief is None:
        raise HTTPException(status_code=404, detail="unknown brief")
    project = _project_of(settings, brief)
    row = _brief_row(settings, brief)
    row.update({
        "events": _store(request).events(brief_id),
        "stages": projects.stages(project, active=brief["state"] == "running"),
        "flow_calls": projects.flow_events(project) if project else [],
        "autopilot_log": projects.autopilot_log(project) if project else "",
        "run_log": projects.tail(project / "factory_run.log", 60) if project else "",
        "images": projects.images(project) if project else [],
        "clips": projects.clips(project) if project else [],
        "release": _staged(brief) or None,
    })
    return row


@api.post("/briefs/{brief_id}/retry")
def retry_brief(brief_id: str, request: Request) -> dict:
    _wrap(actions.retry, _store(request), projects.safe_id(brief_id), notify=_notify(request), source=" (Studio)")
    return {"ok": True}


@api.post("/briefs/{brief_id}/cancel")
def cancel_brief(brief_id: str, request: Request) -> dict:
    _wrap(actions.cancel, _store(request), projects.safe_id(brief_id), notify=_notify(request), source=" (Studio)")
    return {"ok": True}


@api.get("/briefs/{brief_id}/file/{rel:path}")
async def project_file(brief_id: str, rel: str, request: Request, w: Optional[int] = None) -> FileResponse:
    settings: StudioSettings = request.app.state.settings
    brief = _store(request).get(projects.safe_id(brief_id))
    project = _project_of(settings, brief) if brief else None
    if project is None:
        raise HTTPException(status_code=404, detail="no project")
    target = projects.safe_file(project, rel)
    return await _thumb(target, w) if w else FileResponse(target)


@api.get("/videos")
def list_videos(request: Request) -> list[dict]:
    settings: StudioSettings = request.app.state.settings
    rows = []
    for brief in _store(request).list(VIDEO_STATES, limit=300):
        rel = _release_dir(settings, brief["id"])
        if rel is None and brief["state"] == "rejected":
            continue  # rejected before staging: nothing to show
        staged = _staged(brief)
        meta = _release_meta(rel)
        rows.append({"id": brief["id"], "state": brief["state"], "text": brief["text"],
                     "created_at": brief["created_at"], "updated_at": brief["updated_at"],
                     "duration": staged.get("duration"), "caption": meta.get("caption", staged.get("caption", "")),
                     "hashtags": meta.get("hashtags", staged.get("hashtags", [])),
                     "platforms": meta.get("platforms", []), "files": meta.get("files", []),
                     "available": rel is not None})
    return rows


@api.get("/videos/{brief_id}/file/{name}")
async def video_file(brief_id: str, name: str, request: Request, w: Optional[int] = None) -> FileResponse:
    settings: StudioSettings = request.app.state.settings
    rel = _release_dir(settings, projects.safe_id(brief_id))
    if rel is None:
        raise HTTPException(status_code=404, detail="no release files")
    target = _safe_child(rel, name)
    return await _thumb(target, w) if w else FileResponse(target)


@api.patch("/videos/{brief_id}")
def edit_caption(brief_id: str, body: CaptionIn, request: Request) -> dict:
    settings: StudioSettings = request.app.state.settings
    return _wrap(actions.update_caption, settings.factory, _store(request), projects.safe_id(brief_id),
                 body.caption, body.hashtags)


@api.post("/videos/{brief_id}/approve")
def approve_video(brief_id: str, request: Request) -> dict:
    settings: StudioSettings = request.app.state.settings
    return _wrap(actions.approve, settings.factory, _store(request), projects.safe_id(brief_id),
                 notify=_notify(request), source=" в Studio")


@api.post("/videos/{brief_id}/reject")
def reject_video(brief_id: str, request: Request) -> dict:
    settings: StudioSettings = request.app.state.settings
    _wrap(actions.reject, settings.factory, _store(request), projects.safe_id(brief_id),
          notify=_notify(request), source=" в Studio")
    return {"ok": True}


@api.get("/events")
async def events(request: Request) -> StreamingResponse:
    """One SSE feed: `briefs` when the queue changes, `project` when a project's files change."""
    from backlot.server import hub

    store = _store(request)

    async def stream():
        q = hub.subscribe()
        last_sig = await asyncio.to_thread(store.signature)
        last_beat = time.monotonic()
        try:
            yield _sse({"type": "hello"})
            while not await request.is_disconnected():
                changed: set[str] = set()
                with suppress(asyncio.TimeoutError):
                    changed.add(await asyncio.wait_for(q.get(), timeout=SSE_POLL_S))
                while not q.empty():
                    changed.add(q.get_nowait())
                for pid in sorted(changed):
                    yield _sse({"type": "project", "id": pid})
                sig = await asyncio.to_thread(store.signature)
                if sig != last_sig:
                    last_sig = sig
                    yield _sse({"type": "briefs"})
                if time.monotonic() - last_beat > SSE_HEARTBEAT_S:
                    last_beat = time.monotonic()
                    yield _sse({"type": "heartbeat"})
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# -- app ------------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(app: FastAPI):
    from backlot.server import _watch_projects

    task = asyncio.create_task(_watch_projects())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app(settings: Optional[StudioSettings] = None) -> FastAPI:
    settings = settings or studio_config.load()
    app = FastAPI(title="Studio", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)
    app.state.settings = settings
    app.state.store = BriefStore(settings.factory.db_path)
    app.state.limiter = auth.LoginLimiter()
    fs = settings.factory
    app.state.tg = Telegram(fs.tg_token, fs.tg_chat, fs.tg_proxy)
    app.include_router(public)
    app.include_router(api)

    dist = settings.web_dist
    if dist.is_dir():  # in production nginx serves the SPA; this is for local runs
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(status_code=404)
            target = (dist / path).resolve()
            if path and target.is_file() and target.is_relative_to(dist.resolve()):
                return FileResponse(target)
            return FileResponse(dist / "index.html")
    return app


def __getattr__(name: str):  # `uvicorn studio.app:app` builds the app lazily from the environment
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
