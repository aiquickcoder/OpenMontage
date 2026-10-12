"""Worker loop (briefs → pipeline → staged release) and Telegram bot (briefs in, approvals)."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

from factory import actions, release, runner
from factory.config import Settings
from factory.db import BriefStore, InvalidTransition
from factory.tg import Telegram, approval_buttons

log = logging.getLogger("factory")
OPTION_KEYS = {"bait": "comment_bait", "comment_bait": "comment_bait", "product": "product",
               "duration": "duration", "audience": "audience", "style": "style",
               "music": "music",
               # Studio add-ons: character library ids (comma-separated) and ad campaigns
               "characters": "characters", "campaign": "campaign",
               "advertiser": "advertiser", "insert": "insert"}
BAIT_MODES = {"hero_breaks_wall", "meta_joke", "none"}


def parse_brief(text: str) -> tuple[str, dict[str, Any]]:
    """Leading `key: value` lines (bait/product/duration/audience/style) become options."""
    options: dict[str, Any] = {}
    body: list[str] = []
    for line in text.strip().splitlines():
        m = re.match(r"^\s*([a-z_]+)\s*:\s*(.+)$", line)
        if m and not body and m.group(1) in OPTION_KEYS:
            options[OPTION_KEYS[m.group(1)]] = m.group(2).strip()
        else:
            body.append(line)
    if options.get("comment_bait") not in (None, *BAIT_MODES):
        raise ValueError(f"bait must be one of {sorted(BAIT_MODES)}")
    brief = "\n".join(body).strip()
    if len(brief) < 10:
        raise ValueError("бриф слишком короткий")
    return brief, options


def _ledger(project_dir: Optional[str]) -> dict[str, Any]:
    if not project_dir:
        return {}
    path = Path(project_dir) / "flow_credits.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    spent = sum(e["credits"] for e in data.get("entries", []) if e.get("status") == "charged")
    return {"budget": data.get("budget"), "spent": spent}


def apply_ad_marking(settings: Settings, brief: dict[str, Any], staged: dict[str, Any]) -> dict[str, Any]:
    """Append the advertising marking to the staged release (see actions.mark_release). Raises when
    an ad brief's data cannot be resolved, so the brief fails instead of going out unmarked."""
    return actions.mark_release(settings, brief, staged)


def process_one(settings: Settings, store: BriefStore, tg: Telegram, run=runner.run_brief) -> Optional[str]:
    brief = store.claim_next()
    if brief is None:
        return None
    tg.send(f"▶️ Начал: {brief['id']}\n{brief['text'][:300]}")
    try:
        outcome = run(settings, brief)
    except Exception as exc:  # runner crash: never re-run automatically
        log.exception("runner crashed")
        store.move(brief["id"], "failed", detail=f"runner crashed: {exc}")
        tg.send(f"💥 {brief['id']}: сбой запуска — {exc}")
        return brief["id"]

    project_path = Path(outcome.project_dir)
    if not project_path.is_absolute():  # the agent reports "projects/<id>" relative to the repo
        project_path = settings.repo / project_path
    project = str(project_path)
    credits = _ledger(project)
    spent = f"\nКредиты: {credits.get('spent')} / {credits.get('budget')}" if credits else ""
    if outcome.status == "BUDGET":
        store.move(brief["id"], "budget", detail=outcome.detail, project_dir=project)
        tg.send(f"💸 {brief['id']}: не хватило кредитов Flow. {outcome.detail}{spent}")
    elif outcome.status != "READY":
        store.move(brief["id"], "failed", detail=outcome.detail, project_dir=project)
        tg.send(f"❌ {brief['id']}: {outcome.detail}{spent}\nЛог: {project}/factory_run.log")
    else:
        try:
            staged = release.stage(Path(project), settings.releases_dir, brief["id"])
        except release.ReleaseError as exc:
            store.move(brief["id"], "failed", detail=f"release check: {exc}", project_dir=project)
            tg.send(f"❌ {brief['id']}: ролик не прошёл проверку — {exc}")
            return brief["id"]
        try:
            staged = apply_ad_marking(settings, brief, staged)
        except Exception as exc:  # noqa: BLE001 — never publish an ad without its marking
            log.exception("ad marking failed")
            store.move(brief["id"], "failed", detail=f"ad marking: {exc}", project_dir=project)
            tg.send(f"❌ {brief['id']}: не удалось добавить рекламную маркировку — {exc}")
            return brief["id"]
        store.move(brief["id"], "awaiting_approval", detail=json.dumps(staged, ensure_ascii=False),
                   project_dir=project)
        caption = (f"🎬 {brief['id']} · {staged['duration']} с{spent}\n\n{staged['caption']}\n"
                   + " ".join(f"#{h.lstrip('#')}" for h in staged["hashtags"]))
        tg.send_video(Path(staged["video"]), caption, approval_buttons(brief["id"]))
    return brief["id"]


def worker_loop(settings: Settings, poll_s: float = 30) -> None:
    store = BriefStore(settings.db_path)
    tg = Telegram(settings.tg_token, settings.tg_chat, settings.tg_proxy)
    stuck = store.recover_running()
    if stuck:
        tg.send("⚠️ Воркер перезапущен, помечены как failed (проверьте кредиты): " + ", ".join(stuck))
    while True:
        if process_one(settings, store, tg) is None:
            run_studio_tasks(settings)
            time.sleep(poll_s)


def run_studio_tasks(settings: Settings) -> int:
    """Studio background tasks (character sheet generation) — only between briefs."""
    try:
        from studio import characters

        return characters.run_pending_tasks(settings)
    except Exception:  # noqa: BLE001
        log.exception("studio tasks failed")
        return 0


def handle_update(settings: Settings, store: BriefStore, tg: Telegram, update: dict[str, Any]) -> None:
    if "callback_query" in update:
        cq = update["callback_query"]
        if str(cq.get("message", {}).get("chat", {}).get("id")) != tg.chat:
            return
        action, _, brief_id = (cq.get("data") or "").partition(":")
        notes: list[str] = []
        try:
            if action == "approve":
                actions.approve(settings, store, brief_id, notify=notes.append)
            elif action == "reject":
                actions.reject(settings, store, brief_id, notify=notes.append)
            else:
                return
        except (actions.ActionError, InvalidTransition, KeyError) as exc:
            notes.append(f"⚠️ {exc}")
        note = notes[-1] if notes else "ok"
        tg.call("answerCallbackQuery", callback_query_id=cq["id"], text=note[:190])
        tg.send(note)
        return

    msg = update.get("message") or {}
    if str(msg.get("chat", {}).get("id")) != tg.chat:
        return  # only the owner's chat may drive the factory
    text = (msg.get("text") or "").strip()
    if text.startswith("/brief"):
        try:
            brief, options = parse_brief(text[len("/brief"):])
        except ValueError as exc:
            tg.send(f"Не принял: {exc}\nФормат:\n/brief\nbait: hero_breaks_wall\nproduct: …\nТекст брифа")
            return
        if "campaign" in options:  # same checks and expansion as the Studio brief form
            from studio import ads

            try:
                options.update(ads.campaign_options(ads.store_for(runner.studio_settings(settings)), options))
            except ads.CampaignRejected as exc:
                tg.send(f"Не принял: кампания «{options['campaign']}» — {exc.detail}")
                return
        brief_id = store.add(brief, options)
        queued = len(store.list(("queued",), limit=1000))
        tg.send(f"📝 В очереди: {brief_id} (позиция {queued})")
    elif text.startswith("/status"):
        rows = store.list(limit=10)
        tg.send("\n".join(f"{r['state']:<18} {r['id']}" for r in rows) or "Пусто")
    elif text.startswith("/retry"):
        brief_id = text.split(maxsplit=1)[-1].strip()
        try:
            actions.retry(store, brief_id, notify=tg.send)
        except (actions.ActionError, InvalidTransition, KeyError) as exc:
            tg.send(f"⚠️ {exc}")
    elif text.startswith(("/start", "/help")):
        tg.send("/brief <текст> — новый ролик (строки bait:/product:/duration: в начале — опции)\n"
                "/status — последние брифы\n/retry <id> — перезапустить failed/budget")


def bot_loop(settings: Settings) -> None:
    store = BriefStore(settings.db_path)
    tg = Telegram(settings.tg_token, settings.tg_chat, settings.tg_proxy)
    if not tg.enabled:
        raise SystemExit("TG_TOKEN / TG_CHAT are not set")
    offset = 0
    while True:
        try:
            updates = tg.call("getUpdates", http_timeout=75, offset=offset, timeout=60,
                              allowed_updates=["message", "callback_query"])
        except Exception:
            log.exception("getUpdates failed")
            time.sleep(10)
            continue
        for update in updates:
            offset = max(offset, update["update_id"] + 1)
            try:
                handle_update(settings, store, tg, update)
            except Exception:
                log.exception("update failed")
