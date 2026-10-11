"""Owner actions on briefs and releases, shared by the Telegram bot and the Studio web app.

Every action validates the brief state through BriefStore.move, so a click in the web
and a button in Telegram can never disagree. `notify` (optional) receives a one-line
human message, e.g. Telegram.send, so actions taken in the web still reach the chat.
"""

from __future__ import annotations

import json
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


def approve(settings: Settings, store: BriefStore, brief_id: str, notify: Notify = None,
            source: str = "") -> dict[str, Any]:
    _require(store, brief_id)
    store.check(brief_id, "approved")
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
    path = _pending_publish(settings, brief_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["artifacts"]["publish_log"]["entries"]:
        meta = entry.setdefault("metadata_used", {})
        meta["description"] = caption
        meta["hashtags"] = tags
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    staged = json.loads(brief.get("detail") or "{}") if (brief.get("detail") or "").startswith("{") else {}
    staged.update({"caption": caption, "hashtags": tags})
    store.set_detail(brief_id, json.dumps(staged, ensure_ascii=False))
    return staged
