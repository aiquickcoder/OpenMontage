"""Advertising: advertisers, inserts (versioned prompt templates) and campaigns with a client report.

Storage: tables in studio.db (WAL); advertiser files in <data_dir>/advertisers/<id>/
(logo.<ext>, products/*). A campaign pins one insert version, so later edits of the
template never change what an already planned campaign renders.

Factory hooks (wired by the integrator, nothing here imports the runner):
    prompt_lines(settings, options) -> list[str]   extra prompt lines for ad briefs
    marking_for(settings, options) -> str | None   «Реклама. …» line for the caption

Wiring (studio/app.py): `app.include_router(ads.router)`.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import secrets
import sqlite3
import string
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator

from factory.db import BriefStore, make_id
from studio import auth, projects
from studio.config import StudioSettings

PLACEHOLDERS = ("brand", "product", "cta", "legal_name", "must", "must_not", "link", "promo_code")
PLACEMENTS = ("plot", "hero_uses_product", "packshot", "caption_comment")
CAMPAIGN_STATUSES = ("draft", "active", "done")
MAX_UPLOAD = 15 * 1024 * 1024
IMAGE_EXT = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}
MAX_BRIEFS_PER_CALL = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS advertisers (
    id TEXT PRIMARY KEY,
    brand TEXT NOT NULL,
    legal_name TEXT NOT NULL DEFAULT '',
    inn TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    promo_code TEXT NOT NULL DEFAULT '',
    cta TEXT NOT NULL DEFAULT '',
    must_say TEXT NOT NULL DEFAULT '[]',
    must_not_say TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS inserts (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS insert_versions (
    insert_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    placement TEXT NOT NULL,
    max_screen_share REAL NOT NULL,
    template TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (insert_id, version)
);
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    advertiser_id TEXT NOT NULL,
    insert_id TEXT NOT NULL,
    insert_version INTEGER NOT NULL,
    erid TEXT NOT NULL DEFAULT '',
    erid_not_required INTEGER NOT NULL DEFAULT 0,
    product TEXT NOT NULL DEFAULT '',
    period_start TEXT,
    period_end TEXT,
    planned_videos INTEGER NOT NULL DEFAULT 0,
    accounts TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


class TemplateError(ValueError):
    pass


# -- templates ------------------------------------------------------------------
def template_fields(template: str) -> list[str]:
    """Placeholder names in `template`; TemplateError on anything but plain known {name}."""
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:  # stray { or }
        raise TemplateError(f"сломанные скобки: {exc} (литеральные скобки пишутся как {{{{ и }}}})")
    names: list[str] = []
    for _, name, spec, conv in parsed:
        if name is None:
            continue
        if name not in PLACEHOLDERS or spec or conv:
            raise TemplateError(f"неизвестная подстановка {{{name}}}; доступны: "
                                + ", ".join(f"{{{p}}}" for p in PLACEHOLDERS))
        names.append(name)
    return names


def render(template: str, advertiser: dict[str, Any], campaign: Optional[dict[str, Any]] = None) -> str:
    template_fields(template)
    campaign = campaign or {}
    values = {
        "brand": advertiser.get("brand", ""),
        "product": campaign.get("product") or advertiser.get("brand", ""),
        "cta": advertiser.get("cta", ""),
        "legal_name": advertiser.get("legal_name") or advertiser.get("brand", ""),
        "must": "; ".join(advertiser.get("must_say") or []),
        "must_not": "; ".join(advertiser.get("must_not_say") or []),
        "link": advertiser.get("link", ""),
        "promo_code": advertiser.get("promo_code", ""),
    }
    return template.format_map(values)


def marking_line(advertiser: dict[str, Any], erid: str = "") -> str:
    """«Реклама. ООО «Ромашка», ИНН 7700000000. erid: 2Vtzq…» — missing parts are left out."""
    name = (advertiser.get("legal_name") or advertiser.get("brand") or "").strip()
    inn = (advertiser.get("inn") or "").strip()
    who = ", ".join(p for p in (name, f"ИНН {inn}" if inn else "") if p)
    parts = ["Реклама"] + ([who] if who else []) + ([f"erid: {erid.strip()}"] if erid.strip() else [])
    return ". ".join(parts)


# -- models ---------------------------------------------------------------------
def _clean_list(items: Optional[list[str]]) -> Optional[list[str]]:
    if items is None:
        return None
    return [s.strip() for s in items if s and s.strip()]


class AdvertiserIn(BaseModel):
    brand: Optional[str] = Field(default=None, min_length=1, max_length=200)
    legal_name: Optional[str] = Field(default=None, max_length=300)
    inn: Optional[str] = Field(default=None, max_length=12)
    link: Optional[str] = Field(default=None, max_length=500)
    promo_code: Optional[str] = Field(default=None, max_length=100)
    cta: Optional[str] = Field(default=None, max_length=300)
    must_say: Optional[list[str]] = Field(default=None, max_length=30)
    must_not_say: Optional[list[str]] = Field(default=None, max_length=30)
    notes: Optional[str] = Field(default=None, max_length=4000)
    archived: Optional[bool] = None

    @field_validator("inn")
    @classmethod
    def _inn(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        if v and not re.fullmatch(r"\d{10}|\d{12}", v):
            raise ValueError("ИНН: 10 или 12 цифр")
        return v

    @field_validator("must_say", "must_not_say")
    @classmethod
    def _lines(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        v = _clean_list(v)
        if v and any(len(s) > 300 for s in v):
            raise ValueError("строка длиннее 300 символов")
        return v


class InsertIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    placement: Optional[Literal["plot", "hero_uses_product", "packshot", "caption_comment"]] = None
    max_screen_share: Optional[float] = Field(default=None, ge=0, le=0.5)
    template: Optional[str] = Field(default=None, min_length=1, max_length=6000)
    archived: Optional[bool] = None


class CampaignIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    advertiser_id: Optional[str] = Field(default=None, max_length=64)
    insert_id: Optional[str] = Field(default=None, max_length=64)
    insert_version: Optional[int] = Field(default=None, ge=1)
    erid: Optional[str] = Field(default=None, max_length=200)
    erid_not_required: Optional[bool] = None
    product: Optional[str] = Field(default=None, max_length=300)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    planned_videos: Optional[int] = Field(default=None, ge=0, le=10000)
    accounts: Optional[list[str]] = Field(default=None, max_length=200)
    status: Optional[Literal["draft", "active", "done"]] = None

    @field_validator("accounts")
    @classmethod
    def _accounts(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        v = _clean_list(v)
        if v and any(len(s) > 100 for s in v):
            raise ValueError("имя аккаунта длиннее 100 символов")
        return v


class PreviewIn(BaseModel):
    advertiser_id: str = Field(max_length=64)
    campaign_id: Optional[str] = Field(default=None, max_length=64)
    version: Optional[int] = Field(default=None, ge=1)
    template: Optional[str] = Field(default=None, max_length=6000)  # unsaved editor text


class CampaignBriefsIn(BaseModel):
    text: str = Field(min_length=10, max_length=4000)
    count: int = Field(default=1, ge=1, le=MAX_BRIEFS_PER_CALL)


# -- store ----------------------------------------------------------------------
def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


class AdsStore:
    def __init__(self, db_path: Path, data_dir: Path):
        self.path = Path(db_path)
        self.files_root = Path(data_dir) / "advertisers"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # advertisers
    def adv_dir(self, adv_id: str) -> Path:
        return self.files_root / projects.safe_id(adv_id)

    def _adv_row(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["must_say"] = json.loads(d["must_say"])
        d["must_not_say"] = json.loads(d["must_not_say"])
        d["archived"] = bool(d["archived"])
        folder = self.files_root / d["id"]
        logo = sorted(folder.glob("logo.*")) if folder.is_dir() else []
        d["logo"] = logo[0].name if logo else None
        pdir = folder / "products"
        d["products"] = sorted(f"products/{p.name}" for p in pdir.iterdir() if p.is_file()) if pdir.is_dir() else []
        return d

    def list_advertisers(self, archived: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM advertisers" + ("" if archived else " WHERE archived = 0") + " ORDER BY brand COLLATE NOCASE"
        with self._conn() as c:
            return [self._adv_row(r) for r in c.execute(q).fetchall()]

    def get_advertiser(self, adv_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM advertisers WHERE id = ?", (adv_id,)).fetchone()
        return self._adv_row(row) if row else None

    def save_advertiser(self, adv_id: Optional[str], data: dict[str, Any]) -> str:
        now = time.time()
        cols = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, list) else
                    int(v) if isinstance(v, bool) else v) for k, v in data.items()}
        with self._conn() as c:
            if adv_id is None:
                adv_id = _new_id("adv")
                cols.update(id=adv_id, created_at=now, updated_at=now)
                c.execute(f"INSERT INTO advertisers ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                          tuple(cols.values()))
            elif cols:
                cols["updated_at"] = now
                c.execute(f"UPDATE advertisers SET {','.join(f'{k} = ?' for k in cols)} WHERE id = ?",
                          (*cols.values(), adv_id))
        return adv_id

    # inserts
    def _insert_row(self, head: sqlite3.Row, ver: sqlite3.Row) -> dict[str, Any]:
        return {"id": head["id"], "version": ver["version"], "current_version": head["version"],
                "name": ver["name"], "placement": ver["placement"], "max_screen_share": ver["max_screen_share"],
                "template": ver["template"], "placeholders": sorted(set(template_fields(ver["template"]))),
                "archived": bool(head["archived"]), "created_at": head["created_at"],
                "updated_at": head["updated_at"]}

    def list_inserts(self, archived: bool = False) -> list[dict[str, Any]]:
        q = "SELECT id FROM inserts" + ("" if archived else " WHERE archived = 0")
        with self._conn() as c:
            heads = c.execute(q).fetchall()
        rows = [self.get_insert(h["id"]) for h in heads]
        return sorted((r for r in rows if r), key=lambda r: r["name"].lower())

    def get_insert(self, ins_id: str, version: Optional[int] = None) -> Optional[dict[str, Any]]:
        with self._conn() as c:
            head = c.execute("SELECT * FROM inserts WHERE id = ?", (ins_id,)).fetchone()
            if head is None:
                return None
            ver = c.execute("SELECT * FROM insert_versions WHERE insert_id = ? AND version = ?",
                            (ins_id, version or head["version"])).fetchone()
        return self._insert_row(head, ver) if ver else None

    def insert_versions(self, ins_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM insert_versions WHERE insert_id = ? ORDER BY version DESC",
                             (ins_id,)).fetchall()
        return [dict(r) for r in rows]

    def save_insert(self, ins_id: Optional[str], data: dict[str, Any]) -> str:
        """Create, or store a new version when any template field changed (archived alone is no version)."""
        now = time.time()
        fields = ("name", "placement", "max_screen_share", "template")
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if ins_id is None:
                ins_id, version, base = _new_id("ins"), 1, {}
                c.execute("INSERT INTO inserts (id, version, archived, created_at, updated_at) VALUES (?, 1, 0, ?, ?)",
                          (ins_id, now, now))
            else:
                head = c.execute("SELECT * FROM inserts WHERE id = ?", (ins_id,)).fetchone()
                base = dict(c.execute("SELECT * FROM insert_versions WHERE insert_id = ? AND version = ?",
                                      (ins_id, head["version"])).fetchone())
                version = head["version"] + 1
            merged = {k: data.get(k) if data.get(k) is not None else base.get(k) for k in fields}
            if not base or any(merged[k] != base[k] for k in fields):
                c.execute("INSERT INTO insert_versions (insert_id, version, name, placement, max_screen_share, "
                          "template, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (ins_id, version, merged["name"], merged["placement"], merged["max_screen_share"],
                           merged["template"], now))
                c.execute("UPDATE inserts SET version = ?, updated_at = ? WHERE id = ?", (version, now, ins_id))
            if data.get("archived") is not None:
                c.execute("UPDATE inserts SET archived = ?, updated_at = ? WHERE id = ?",
                          (int(data["archived"]), now, ins_id))
            c.execute("COMMIT")
        return ins_id

    # campaigns
    @staticmethod
    def _camp_row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["accounts"] = json.loads(d["accounts"])
        d["erid_not_required"] = bool(d["erid_not_required"])
        return d

    def list_campaigns(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()
        return [self._camp_row(r) for r in rows]

    def get_campaign(self, cmp_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM campaigns WHERE id = ?", (cmp_id,)).fetchone()
        return self._camp_row(row) if row else None

    def save_campaign(self, cmp_id: Optional[str], data: dict[str, Any]) -> str:
        now = time.time()
        cols = {}
        for k, v in data.items():
            if isinstance(v, list):
                v = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, bool):
                v = int(v)
            elif isinstance(v, date):
                v = v.isoformat()
            cols[k] = v
        with self._conn() as c:
            if cmp_id is None:
                cmp_id = _new_id("cmp")
                cols.update(id=cmp_id, created_at=now, updated_at=now)
                c.execute(f"INSERT INTO campaigns ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                          tuple(cols.values()))
            elif cols:
                cols["updated_at"] = now
                c.execute(f"UPDATE campaigns SET {','.join(f'{k} = ?' for k in cols)} WHERE id = ?",
                          (*cols.values(), cmp_id))
        return cmp_id

    def delete_campaign(self, cmp_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM campaigns WHERE id = ?", (cmp_id,))

    def farm_publications(self, release_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Publish jobs per release from the farm mirror (read-only; empty when the table is absent)."""
        out: dict[str, list[dict[str, Any]]] = {}
        if not release_ids:
            return out
        with self._conn() as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'farm_jobs'").fetchone():
                return out
            rows = c.execute(
                f"SELECT * FROM farm_jobs WHERE kind = 'publish' AND release_id IN "
                f"({','.join('?' * len(release_ids))}) ORDER BY id", release_ids).fetchall()
        for r in rows:
            try:
                result = json.loads(r["result"] or "null") or {}
            except ValueError:
                result = {}
            out.setdefault(r["release_id"], []).append({
                "job_id": r["id"], "account": r["account_id"], "device": r["device"], "status": r["status"],
                "finished_at": r["finished_at"],
                # the farm publish result carries the post link as "url" (same key /api/publications reads)
                "post_url": auth.http_url(result.get("url")) if isinstance(result, dict) else None})
        return out


def store_for(settings: StudioSettings) -> AdsStore:
    return AdsStore(settings.db_path, settings.factory.data_dir)


class CampaignRejected(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def campaign_options(store: AdsStore, opts: dict[str, Any]) -> dict[str, Any]:
    """Options a `campaign:` brief carries (same shape as POST /api/campaigns/{id}/briefs).
    Shared by the Studio brief form and Telegram /brief: an unknown or finished campaign is refused."""
    cmp_id = str(opts.get("campaign") or "")
    campaign = store.get_campaign(cmp_id) if projects.ID_RE.match(cmp_id) and ".." not in cmp_id else None
    if campaign is None:
        raise CampaignRejected(422, "unknown campaign")
    if campaign["status"] == "done":
        raise CampaignRejected(409, "кампания завершена")
    out = {"campaign": campaign["id"], "advertiser": campaign["advertiser_id"],
           "insert": campaign["insert_id"], "insert_version": campaign["insert_version"]}
    if campaign.get("product") and not opts.get("product"):
        out["product"] = campaign["product"]
    return out


# -- helpers --------------------------------------------------------------------
def _settings(request: Request) -> StudioSettings:
    return request.app.state.settings


def _ads(request: Request) -> AdsStore:
    store = getattr(request.app.state, "ads", None)
    if store is None:
        store = request.app.state.ads = store_for(_settings(request))
    return store


def _need(value: Optional[dict[str, Any]], what: str) -> dict[str, Any]:
    if value is None:
        raise HTTPException(status_code=404, detail=f"{what} не найден")
    return value


def _check_template(template: Optional[str]) -> None:
    if template is None:
        return
    try:
        template_fields(template)
    except TemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _iso(ts: Optional[float]) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _image_ext(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return IMAGE_EXT["png"]
    if data.startswith(b"\xff\xd8\xff"):
        return IMAGE_EXT["jpeg"]
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return IMAGE_EXT["webp"]
    return None


def parse_multipart(content_type: str, body: bytes) -> dict[str, tuple[Optional[str], bytes]]:
    """Minimal multipart/form-data parser (stdlib only): {field: (filename, data)}."""
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not content_type.startswith("multipart/form-data") or not m:
        raise HTTPException(status_code=415, detail="нужен multipart/form-data")
    delim = b"--" + m.group(1).encode()
    fields: dict[str, tuple[Optional[str], bytes]] = {}
    for part in body.split(delim)[1:]:
        if part.startswith(b"--"):
            break
        head, sep, data = part.removeprefix(b"\r\n").partition(b"\r\n\r\n")
        if not sep:
            continue
        data = data.removesuffix(b"\r\n")
        disp = next((ln for ln in head.decode("utf-8", "replace").split("\r\n")
                     if ln.lower().startswith("content-disposition:")), "")
        name = re.search(r'\bname="([^"]*)"', disp)
        filename = re.search(r'\bfilename="([^"]*)"', disp)
        if name:
            fields[name.group(1)] = (filename.group(1) if filename else None, data)
    return fields


def _brief_ids_for(settings: StudioSettings, campaign_id: str) -> list[dict[str, Any]]:
    """Briefs whose options carry this campaign (factory.db, read-only query)."""
    store = BriefStore(settings.factory.db_path)
    with store._conn() as c:
        rows = c.execute("SELECT * FROM briefs WHERE json_extract(options, '$.campaign') = ? "
                         "ORDER BY created_at", (campaign_id,)).fetchall()
    return [store._row(r) for r in rows]


def _approved_index(settings: StudioSettings) -> dict[str, str]:
    path = settings.factory.releases_dir / "index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {r.get("id"): r.get("approved_at") or "" for r in data.get("releases", []) if r.get("id")}


def _caption(settings: StudioSettings, brief: dict[str, Any]) -> str:
    root = settings.factory.releases_dir
    for path in (root / brief["id"] / "checkpoint_publish.json", root / "pending" / brief["id"] / "checkpoint_publish.json"):
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entries = ((data.get("artifacts") or {}).get("publish_log") or {}).get("entries") or []
                first = (entries[0].get("metadata_used") or {}) if entries else {}
                return first.get("description") or first.get("title") or ""
            except (OSError, ValueError, AttributeError):
                pass
    detail = brief.get("detail") or ""
    if detail.startswith("{"):
        try:
            return json.loads(detail).get("caption") or ""
        except ValueError:
            pass
    return ""


def build_report(settings: StudioSettings, store: AdsStore, campaign: dict[str, Any]) -> dict[str, Any]:
    adv = store.get_advertiser(campaign["advertiser_id"]) or {}
    briefs = _brief_ids_for(settings, campaign["id"])
    approved = _approved_index(settings)
    pubs = store.farm_publications([b["id"] for b in briefs])
    rows = [{"brief_id": b["id"], "state": b["state"], "created_at": b["created_at"],
             "approved_at": approved.get(b["id"]) or None, "caption": _caption(settings, b),
             "publications": pubs.get(b["id"], [])} for b in briefs]
    published = sum(1 for r in rows for p in r["publications"] if p["status"] == "done")
    return {
        "campaign": {"id": campaign["id"], "name": campaign["name"], "status": campaign["status"],
                     "erid": campaign["erid"], "erid_not_required": campaign["erid_not_required"],
                     "period_start": campaign["period_start"], "period_end": campaign["period_end"],
                     "planned_videos": campaign["planned_videos"], "product": campaign["product"]},
        "advertiser": {"id": adv.get("id"), "brand": adv.get("brand", ""), "legal_name": adv.get("legal_name", ""),
                       "inn": adv.get("inn", "")},
        "marking": marking_line(adv, campaign["erid"]) if adv else None,
        "totals": {"briefs": len(rows), "approved": sum(1 for r in rows if r["approved_at"] or r["state"] == "approved"),
                   "publications": published},
        "rows": rows,
    }


def _cell(v: Any) -> str:
    """CSV cell safe for Excel/LibreOffice: text starting with = + - @ (or tab/CR) is not a formula."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


class _SafeWriter:
    def __init__(self, w: Any):
        self._w = w

    def writerow(self, row: list[Any]) -> None:
        self._w.writerow([_cell(v) for v in row])


def report_csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = _SafeWriter(csv.writer(buf, delimiter=";", lineterminator="\r\n"))
    cmp, adv = report["campaign"], report["advertiser"]
    w.writerow(["Кампания", cmp["name"]])
    w.writerow(["Рекламодатель", adv["legal_name"] or adv["brand"]])
    w.writerow(["ИНН", adv["inn"]])
    w.writerow(["ERID", cmp["erid"] or ("не требуется" if cmp["erid_not_required"] else "")])
    w.writerow(["Период", f"{cmp['period_start'] or ''} — {cmp['period_end'] or ''}"])
    w.writerow(["Роликов план / сделано / публикаций", cmp["planned_videos"], report["totals"]["approved"],
                report["totals"]["publications"]])
    w.writerow([])
    w.writerow(["Бриф", "Состояние", "Создан", "Одобрен", "Подпись", "Аккаунт", "Устройство",
                "Статус публикации", "Опубликовано", "Ссылка"])
    for r in report["rows"]:
        base = [r["brief_id"], r["state"], r["created_at"], r["approved_at"] or "", r["caption"]]
        for p in r["publications"] or [None]:
            w.writerow(base + ([p["account"], p["device"], p["status"], _iso(p["finished_at"]), p["post_url"] or ""]
                               if p else ["", "", "", "", ""]))
    return "﻿" + buf.getvalue()


# -- factory hooks --------------------------------------------------------------
def _resolve(store: AdsStore, options: dict[str, Any]) -> Optional[tuple[dict, dict, Optional[dict]]]:
    """(advertiser, insert at the pinned version, campaign|None) for an ad brief, else None."""
    campaign = None
    if options.get("campaign"):
        campaign = store.get_campaign(str(options["campaign"]))
        if campaign is None:
            return None
        adv_id, ins_id, ver = campaign["advertiser_id"], campaign["insert_id"], campaign["insert_version"]
    elif options.get("advertiser") and options.get("insert"):
        adv_id, ins_id = str(options["advertiser"]), str(options["insert"])
        ver = int(options["insert_version"]) if options.get("insert_version") else None
    else:
        return None
    adv = store.get_advertiser(adv_id)
    ins = store.get_insert(ins_id, ver)
    if adv is None or ins is None:
        return None
    return adv, ins, campaign


def advertiser_json(store: AdsStore, adv: dict[str, Any], campaign: Optional[dict[str, Any]]) -> Path:
    """Write <data_dir>/advertisers/<id>/advertiser.json for the agent (absolute file paths)."""
    folder = store.adv_dir(adv["id"])
    folder.mkdir(parents=True, exist_ok=True)
    campaign = campaign or {}
    data = {
        "brand": adv["brand"],
        "product": campaign.get("product") or adv["brand"],
        "legal_name": adv["legal_name"] or adv["brand"],
        "inn": adv["inn"],
        "erid": campaign.get("erid", ""),
        "erid_not_required": bool(campaign.get("erid_not_required")),
        "cta": adv["cta"],
        "link": adv["link"],
        "promo_code": adv["promo_code"],
        "mandatory_lines": adv["must_say"],
        "forbidden_claims": adv["must_not_say"],
        "logo_path": str((folder / adv["logo"]).resolve()) if adv["logo"] else None,
        "product_photos": [str((folder / p).resolve()) for p in adv["products"]],
        "marking": marking_line(adv, campaign.get("erid", "")),
        "campaign_id": campaign.get("id"),
        "notes": adv["notes"],
    }
    path = folder / "advertiser.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path.resolve()


def prompt_lines(settings: StudioSettings, options: dict[str, Any]) -> list[str]:
    """Extra build_prompt lines for an ad brief (campaign, or advertiser+insert); [] otherwise."""
    if not (options.get("campaign") or (options.get("advertiser") and options.get("insert"))):
        return []
    store = store_for(settings)
    resolved = _resolve(store, options)
    if resolved is None:
        return ["- AD DATA MISSING: this brief references an advertiser campaign that no longer exists "
                "in the Studio. Do not invent advertiser data — stop with "
                "`AUTOPILOT_RESULT: FAILED projects/<id> ad data missing`."]
    adv, ins, campaign = resolved
    path = advertiser_json(store, adv, campaign)
    lines = [
        f"- Apply addons/ad-insert/README.md. Advertiser data: {path} "
        "(fill brief.metadata.advertiser from it verbatim; never use placeholders).",
        f"- Ad insert `{ins['id']}` v{ins['version']}: placement `{ins['placement']}`, "
        f"max screen share {ins['max_screen_share']:.0%} of the film's runtime.",
        "- Insert instructions (rendered, follow literally):",
        *("  " + ln for ln in render(ins["template"], adv, campaign).splitlines()),
    ]
    if campaign and not campaign["erid"] and not campaign["erid_not_required"]:
        lines.append("- ERID is not issued yet: leave `erid` empty, keep the «Реклама» + legal name marking, "
                     "and note «ERID pending» in the publish summary.")
    return lines


def marking_for(settings: StudioSettings, options: dict[str, Any]) -> Optional[str]:
    """Caption marking line for ad briefs; None for ordinary briefs."""
    if not (options.get("campaign") or (options.get("advertiser") and options.get("insert"))):
        return None
    resolved = _resolve(store_for(settings), options)
    if resolved is None:
        return None
    adv, _, campaign = resolved
    return marking_line(adv, (campaign or {}).get("erid", ""))


def erid_pending(settings: StudioSettings, options: dict[str, Any]) -> bool:
    """True when the brief's campaign still needs an ERID (not issued, not marked as not required)."""
    resolved = _resolve(store_for(settings), options)
    campaign = resolved[2] if resolved else None
    return bool(campaign and not campaign["erid"] and not campaign["erid_not_required"])


# -- router ---------------------------------------------------------------------
router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])


@router.get("/ads/meta")
def ads_meta() -> dict:
    return {"placeholders": list(PLACEHOLDERS), "placements": list(PLACEMENTS),
            "statuses": list(CAMPAIGN_STATUSES), "max_upload": MAX_UPLOAD}


# advertisers
@router.get("/advertisers")
def list_advertisers(request: Request, archived: bool = False) -> list[dict]:
    return _ads(request).list_advertisers(archived)


@router.post("/advertisers", status_code=201)
def create_advertiser(body: AdvertiserIn, request: Request) -> dict:
    data = body.model_dump(exclude_none=True)
    if not data.get("brand"):
        raise HTTPException(status_code=422, detail="нужен бренд")
    store = _ads(request)
    return _need(store.get_advertiser(store.save_advertiser(None, data)), "рекламодатель")


@router.get("/advertisers/{adv_id}")
def get_advertiser(adv_id: str, request: Request) -> dict:
    return _need(_ads(request).get_advertiser(projects.safe_id(adv_id)), "рекламодатель")


@router.patch("/advertisers/{adv_id}")
def update_advertiser(adv_id: str, body: AdvertiserIn, request: Request) -> dict:
    store = _ads(request)
    _need(store.get_advertiser(projects.safe_id(adv_id)), "рекламодатель")
    store.save_advertiser(adv_id, body.model_dump(exclude_unset=True, exclude_none=True))
    return store.get_advertiser(adv_id)


@router.delete("/advertisers/{adv_id}")
def archive_advertiser(adv_id: str, request: Request) -> dict:
    store = _ads(request)
    _need(store.get_advertiser(projects.safe_id(adv_id)), "рекламодатель")
    store.save_advertiser(adv_id, {"archived": True})
    return {"ok": True}


@router.post("/advertisers/{adv_id}/files", status_code=201)
async def upload_advertiser_file(adv_id: str, request: Request, kind: Optional[str] = None) -> dict:
    """multipart/form-data: `file` (png/jpg/webp ≤15 MB) and `kind` = logo|product (field or ?kind=)."""
    store = _ads(request)
    _need(store.get_advertiser(projects.safe_id(adv_id)), "рекламодатель")
    if int(request.headers.get("content-length") or 0) > MAX_UPLOAD + 64 * 1024:
        raise HTTPException(status_code=413, detail="файл больше 15 МБ")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_UPLOAD + 64 * 1024:
            raise HTTPException(status_code=413, detail="файл больше 15 МБ")
    fields = parse_multipart(request.headers.get("content-type", ""), bytes(body))
    kind = kind or (fields.get("kind", (None, b""))[1].decode("utf-8", "replace").strip())
    if kind not in ("logo", "product"):
        raise HTTPException(status_code=422, detail="kind: logo или product")
    filename, data = fields.get("file", (None, b""))
    if not data:
        raise HTTPException(status_code=422, detail="нет файла")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="файл больше 15 МБ")
    ext = _image_ext(data)
    if ext is None:
        raise HTTPException(status_code=415, detail="только PNG, JPG или WEBP")
    folder = store.adv_dir(adv_id)
    if kind == "logo":
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("logo.*"):
            old.unlink()
        target = folder / f"logo{ext}"
    else:
        (folder / "products").mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(filename or "photo").stem).strip("-")[:40] or "photo"
        target = folder / "products" / f"{stem}-{secrets.token_hex(3)}{ext}"
    target.write_bytes(data)
    store.save_advertiser(adv_id, {"archived": store.get_advertiser(adv_id)["archived"]})  # bump updated_at
    return {"path": str(target.relative_to(folder)), "advertiser": store.get_advertiser(adv_id)}


def _adv_file(store: AdsStore, adv_id: str, rel: str) -> Path:
    folder = store.adv_dir(adv_id).resolve()
    target = (folder / rel).resolve()
    try:
        target.relative_to(folder)
    except ValueError:
        raise HTTPException(status_code=403, detail="path escapes advertiser")
    if not target.is_file() or target.name == "advertiser.json":
        raise HTTPException(status_code=404, detail="file not found")
    return target


@router.get("/advertisers/{adv_id}/file/{rel:path}")
async def advertiser_file(adv_id: str, rel: str, request: Request, w: Optional[int] = None) -> FileResponse:
    target = _adv_file(_ads(request), projects.safe_id(adv_id), rel)
    if not w:
        return FileResponse(target)
    from backlot.server import THUMB_WIDTHS, _thumbnail_for

    cached = await asyncio.to_thread(_thumbnail_for, target, min(THUMB_WIDTHS, key=lambda x: abs(x - w)))
    return FileResponse(cached, media_type="image/jpeg") if cached else FileResponse(target)


@router.delete("/advertisers/{adv_id}/file/{rel:path}")
def delete_advertiser_file(adv_id: str, rel: str, request: Request) -> dict:
    store = _ads(request)
    _adv_file(store, projects.safe_id(adv_id), rel).unlink()
    return _need(store.get_advertiser(adv_id), "рекламодатель")


# inserts
@router.get("/inserts")
def list_inserts(request: Request, archived: bool = False) -> list[dict]:
    return _ads(request).list_inserts(archived)


@router.post("/inserts", status_code=201)
def create_insert(body: InsertIn, request: Request) -> dict:
    data = body.model_dump(exclude_none=True)
    missing = [k for k in ("name", "placement", "max_screen_share", "template") if k not in data]
    if missing:
        raise HTTPException(status_code=422, detail=f"не заполнено: {', '.join(missing)}")
    _check_template(data["template"])
    store = _ads(request)
    return store.get_insert(store.save_insert(None, data))


@router.get("/inserts/{ins_id}")
def get_insert(ins_id: str, request: Request, version: Optional[int] = None) -> dict:
    return _need(_ads(request).get_insert(projects.safe_id(ins_id), version), "шаблон")


@router.patch("/inserts/{ins_id}")
def update_insert(ins_id: str, body: InsertIn, request: Request) -> dict:
    store = _ads(request)
    _need(store.get_insert(projects.safe_id(ins_id)), "шаблон")
    _check_template(body.template)
    store.save_insert(ins_id, body.model_dump(exclude_none=True))
    return store.get_insert(ins_id)


@router.delete("/inserts/{ins_id}")
def archive_insert(ins_id: str, request: Request) -> dict:
    store = _ads(request)
    _need(store.get_insert(projects.safe_id(ins_id)), "шаблон")
    store.save_insert(ins_id, {"archived": True})
    return {"ok": True}


@router.get("/inserts/{ins_id}/versions")
def insert_versions(ins_id: str, request: Request) -> list[dict]:
    store = _ads(request)
    _need(store.get_insert(projects.safe_id(ins_id)), "шаблон")
    return store.insert_versions(ins_id)


@router.post("/inserts/{ins_id}/preview")
def preview_insert(ins_id: str, body: PreviewIn, request: Request) -> dict:
    store = _ads(request)
    ins = _need(store.get_insert(projects.safe_id(ins_id), body.version), "шаблон")
    adv = _need(store.get_advertiser(projects.safe_id(body.advertiser_id)), "рекламодатель")
    campaign = _need(store.get_campaign(projects.safe_id(body.campaign_id)), "кампания") if body.campaign_id else None
    template = body.template if body.template is not None else ins["template"]
    _check_template(template)
    return {"text": render(template, adv, campaign), "version": ins["version"],
            "marking": marking_line(adv, (campaign or {}).get("erid", ""))}


# campaigns
def _validate_campaign(store: AdsStore, data: dict[str, Any], current: Optional[dict[str, Any]] = None) -> None:
    merged = {**(current or {}), **data}
    if not store.get_advertiser(merged.get("advertiser_id") or ""):
        raise HTTPException(status_code=422, detail="рекламодатель не найден")
    ins = store.get_insert(merged.get("insert_id") or "")
    if ins is None:
        raise HTTPException(status_code=422, detail="шаблон вставки не найден")
    changed_insert = current is None or data.get("insert_id") not in (None, current["insert_id"])
    if data.get("insert_version") is None and changed_insert:
        data["insert_version"] = ins["current_version"]  # pin the version current at save time
    elif data.get("insert_version") is not None and not store.get_insert(merged["insert_id"], data["insert_version"]):
        raise HTTPException(status_code=422, detail="нет такой версии шаблона")
    start, end = merged.get("period_start"), merged.get("period_end")
    if start and end and str(end) < str(start):
        raise HTTPException(status_code=422, detail="конец периода раньше начала")


@router.get("/campaigns")
def list_campaigns(request: Request) -> list[dict]:
    store = _ads(request)
    advs = {a["id"]: a for a in store.list_advertisers(archived=True)}
    rows = store.list_campaigns()
    for r in rows:
        r["advertiser_brand"] = (advs.get(r["advertiser_id"]) or {}).get("brand", "")
    return rows


@router.post("/campaigns", status_code=201)
def create_campaign(body: CampaignIn, request: Request) -> dict:
    data = body.model_dump(exclude_none=True)
    for key in ("name", "advertiser_id", "insert_id"):
        if not data.get(key):
            raise HTTPException(status_code=422, detail=f"не заполнено: {key}")
    store = _ads(request)
    _validate_campaign(store, data)
    return store.get_campaign(store.save_campaign(None, data))


@router.get("/campaigns/{cmp_id}")
def get_campaign(cmp_id: str, request: Request) -> dict:
    store = _ads(request)
    campaign = _need(store.get_campaign(projects.safe_id(cmp_id)), "кампания")
    campaign["marking"] = marking_for(_settings(request), {"campaign": cmp_id})
    return campaign


@router.patch("/campaigns/{cmp_id}")
def update_campaign(cmp_id: str, body: CampaignIn, request: Request) -> dict:
    store = _ads(request)
    current = _need(store.get_campaign(projects.safe_id(cmp_id)), "кампания")
    data = body.model_dump(exclude_unset=True)
    for key in [k for k, v in data.items() if v is None and k not in ("period_start", "period_end")]:
        data.pop(key)  # null = keep; only the period can be cleared
    _validate_campaign(store, data, current)
    store.save_campaign(cmp_id, data)
    return store.get_campaign(cmp_id)


@router.delete("/campaigns/{cmp_id}")
def delete_campaign(cmp_id: str, request: Request) -> dict:
    store = _ads(request)
    campaign = _need(store.get_campaign(projects.safe_id(cmp_id)), "кампания")
    if campaign["status"] != "draft" or _brief_ids_for(_settings(request), cmp_id):
        raise HTTPException(status_code=409, detail="удалить можно только черновик без брифов")
    store.delete_campaign(cmp_id)
    return {"ok": True}


@router.post("/campaigns/{cmp_id}/briefs", status_code=201)
def create_campaign_briefs(cmp_id: str, body: CampaignBriefsIn, request: Request) -> dict:
    settings, store = _settings(request), _ads(request)
    campaign = _need(store.get_campaign(projects.safe_id(cmp_id)), "кампания")
    if campaign["status"] == "done":
        raise HTTPException(status_code=409, detail="кампания завершена")
    adv = store.get_advertiser(campaign["advertiser_id"])
    if adv is None or adv["archived"]:
        raise HTTPException(status_code=409, detail="рекламодатель в архиве или удалён")
    options = {"campaign": cmp_id, "advertiser": campaign["advertiser_id"], "insert": campaign["insert_id"],
               "insert_version": campaign["insert_version"]}
    if campaign["product"]:
        options["product"] = campaign["product"]
    briefs = BriefStore(settings.factory.db_path)
    text = body.text.strip()
    base = make_id(text)
    ids = []
    for i in range(body.count):
        brief_id = base if body.count == 1 else f"{base}-{i + 1}"
        while briefs.get(brief_id):
            brief_id = f"{base}-{secrets.token_hex(2)}"
        ids.append(briefs.add(text, options, brief_id=brief_id))
    warnings = []
    if not campaign["erid"] and not campaign["erid_not_required"]:
        warnings.append("ERID не указан: одобрение заблокировано, пока его не впишут в кампанию; "
                        "плашка в ролике рендерится без ERID — для ERID в кадре перезапустите бриф после ввода")
    if not adv["logo"]:
        warnings.append("у рекламодателя нет логотипа")
    if not adv["products"]:
        warnings.append("у рекламодателя нет фото продукта")
    return {"ids": ids, "warnings": warnings}


@router.get("/campaigns/{cmp_id}/report")
def campaign_report(cmp_id: str, request: Request) -> dict:
    store = _ads(request)
    campaign = _need(store.get_campaign(projects.safe_id(cmp_id)), "кампания")
    return build_report(_settings(request), store, campaign)


@router.get("/campaigns/{cmp_id}/report.csv")
def campaign_report_csv(cmp_id: str, request: Request) -> Response:
    store = _ads(request)
    campaign = _need(store.get_campaign(projects.safe_id(cmp_id)), "кампания")
    text = report_csv(build_report(_settings(request), store, campaign))
    return Response(content=text.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="campaign-{cmp_id}.csv"'})
