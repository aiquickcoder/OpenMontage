"""Owner actions on briefs and releases, shared by the Telegram bot and the Studio web app.

Every action validates the brief state through BriefStore.move, so a click in the web
and a button in Telegram can never disagree. `notify` (optional) receives a one-line
human message, e.g. Telegram.send, so actions taken in the web still reach the chat.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

from factory import release
from factory.config import Settings
from factory.db import BriefStore

Notify = Optional[Callable[[str], Any]]


class ActionError(RuntimeError):
    pass


def _say(notify: Notify, text: str) -> None:
    if notify:
        try:
            notify(text)
        except Exception:  # notifications are best-effort; the action already happened
            pass


def _require(store: BriefStore, brief_id: str) -> dict[str, Any]:
    brief = store.get(brief_id)
    if brief is None:
        raise ActionError(f"unknown brief {brief_id}")
    return brief


def is_ad(options: Optional[dict[str, Any]]) -> bool:
    opts = options or {}
    return bool(opts.get("campaign") or (opts.get("advertiser") and opts.get("insert")))


_OLD_MARKING = re.compile(r"^Реклама(\.|$)")


def with_marking(text: str, marking: str) -> str:
    """`text` ending with `marking` on its own line. A stale marking line (e.g. one written
    before the ERID was issued) is replaced, never duplicated."""
    text = (text or "").rstrip()
    if marking in text:
        return text
    kept = [ln for ln in text.splitlines() if not _OLD_MARKING.match(ln.strip())]
    text = "\n".join(kept).rstrip()
    return f"{text}\n\n{marking}" if text else marking


def ad_marking(settings: Settings, options: dict[str, Any], for_publish: bool = False) -> Optional[str]:
    """Current marking line of an ad brief (None for ordinary briefs). Raises ActionError when the
    ad data cannot be resolved, or — with `for_publish` — when the campaign still has no ERID."""
    if not is_ad(options):
        return None
    from factory import runner
    from studio import ads

    studio = runner.studio_settings(settings)
    marking = ads.marking_for(studio, options)
    if not marking:
        raise ActionError("рекламные данные не найдены (кампания/рекламодатель/вставка удалены?)")
    if for_publish and ads.erid_pending(studio, options):
        raise ActionError("ERID не указан: впишите его в кампанию (или отметьте «ERID не требуется»)")
    return marking


def mark_release(settings: Settings, brief: dict[str, Any], staged: dict[str, Any],
                 for_publish: bool = False) -> dict[str, Any]:
    """Put the current advertising marking («Реклама. …, ИНН …, erid: …») into the staged caption
    and every publish_log entry of the pending release. Idempotent; a no-op for ordinary briefs."""
    marking = ad_marking(settings, brief.get("options") or {}, for_publish=for_publish)
    if not marking:
        return staged
    staged = dict(staged, caption=with_marking(staged.get("caption", ""), marking))
    path = Path(staged["dir"]) / "checkpoint_publish.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in ((data.get("artifacts") or {}).get("publish_log") or {}).get("entries") or []:
            meta = entry.setdefault("metadata_used", {})
            meta["description"] = with_marking(meta.get("description") or meta.get("title") or "", marking)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    return staged


def _staged(brief: dict[str, Any]) -> dict[str, Any]:
    detail = brief.get("detail") or ""
    return json.loads(detail) if detail.startswith("{") else {}


def approve(settings: Settings, store: BriefStore, brief_id: str, notify: Notify = None,
            source: str = "") -> dict[str, Any]:
    brief = _require(store, brief_id)
    store.check(brief_id, "approved")
    if is_ad(brief.get("options")):  # the ERID may have been filled in after staging
        staged = dict(_staged(brief), dir=str(settings.releases_dir / "pending" / brief_id))
        staged = mark_release(settings, brief, staged, for_publish=True)
        store.set_detail(brief_id, json.dumps(staged, ensure_ascii=False))
    try:
        record = release.approve(settings.releases_dir, brief_id)
    except release.ReleaseError as exc:
        raise ActionError(str(exc)) from exc
    store.move(brief_id, "approved")
    _say(notify, f"✅ {brief_id} одобрен{source} — ферма заберёт ({len(record['files'])} файла)")
    return record


def reject(settings: Settings, store: BriefStore, brief_id: str, notify: Notify = None,
           source: str = "") -> None:
    _require(store, brief_id)
    store.check(brief_id, "rejected")
    release.discard(settings.releases_dir, brief_id)
    store.move(brief_id, "rejected")
    _say(notify, f"🗑 {brief_id} отклонён{source}")


def retry(store: BriefStore, brief_id: str, notify: Notify = None, source: str = "") -> None:
    brief = _require(store, brief_id)
    store.move(brief_id, "queued", detail=f"manual retry after: {brief.get('detail') or brief['state']}"[:2000])
    _say(notify, f"🔁 {brief_id} снова в очереди{source}")


def cancel(store: BriefStore, brief_id: str, notify: Notify = None, source: str = "") -> None:
    """Drop a queued (or failed/budget) brief. Running briefs cannot be cancelled here."""
    _require(store, brief_id)
    store.move(brief_id, "rejected", detail="cancelled by owner")
    _say(notify, f"🚫 {brief_id} отменён{source}")


def _pending_publish(settings: Settings, brief_id: str) -> Path:
    path = settings.releases_dir / "pending" / brief_id / "checkpoint_publish.json"
    if not path.is_file():
        raise ActionError(f"{brief_id}: no pending release to edit (already approved or rejected?)")
    return path


def update_caption(settings: Settings, store: BriefStore, brief_id: str, caption: str,
                   hashtags: list[str]) -> dict[str, Any]:
    """Edit the caption/hashtags of a staged release before approval (all publish entries)."""
    brief = _require(store, brief_id)
    if brief["state"] != "awaiting_approval":
        raise ActionError(f"{brief_id}: caption can only be edited while awaiting approval")
    tags = [t.strip().lstrip("#") for t in hashtags if t.strip()]
    marking = ad_marking(settings, brief.get("options") or {})
    if marking:  # an ad never loses its marking through an owner edit
        caption = with_marking(caption, marking)
    path = _pending_publish(settings, brief_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["artifacts"]["publish_log"]["entries"]:
        meta = entry.setdefault("metadata_used", {})
        meta["description"] = caption
        meta["hashtags"] = tags
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    staged = _staged(brief)
    staged.update({"caption": caption, "hashtags": tags})
    store.set_detail(brief_id, json.dumps(staged, ensure_ascii=False))
    return staged
