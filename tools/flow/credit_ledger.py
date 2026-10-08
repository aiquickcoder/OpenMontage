"""Per-project Flow credit ledger with a hard budget cap.

gflow has no spend limit of its own, and a Google AI Pro subscription has a
fixed monthly pool (1000 credits at the time of writing), so every paid Flow
call reserves credits here first and is refused once the project budget would
be exceeded. A reservation is refunded only when gflow proves the failure was
pre-submit (see gflow_client.PRE_SUBMIT_EXIT_CODES).

Ledger file: projects/<id>/flow_credits.json (or output/flow_credits.json for
calls that cannot be attributed to a project).
"""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from lib.paths import REPO_ROOT

# Credits per generated clip, by subscription plan (FLOW_PLAN) and gflow model
# alias. Pro numbers match gflow's own error table; Ultra halves Lite and adds
# the zero-credit "Veo 3.1 - Lite [Lower Priority]" queue (Ultra subscribers
# and family managers only). Fast/Quality Ultra discounts are not published,
# and omni-flash is not priced anywhere — kept at Pro rates until measured.
PLAN_VIDEO_CREDITS = {
    "pro": {
        "veo-lite": 10,
        "veo-fast": 20,
        "veo-quality": 100,
        "omni-flash": 20,
    },
    "ultra": {
        "veo-lite-lp": 0,
        "veo-lite": 5,
        "veo-fast": 20,
        "veo-quality": 100,
        "omni-flash": 20,
    },
}
ALL_VIDEO_MODELS = sorted({m for costs in PLAN_VIDEO_CREDITS.values() for m in costs})
EXTEND_CREDITS = 10

DEFAULT_BUDGET = 500
LEDGER_FILENAME = "flow_credits.json"


class FlowBudgetExceeded(RuntimeError):
    pass


def flow_plan() -> str:
    plan = os.environ.get("FLOW_PLAN", "pro").strip().lower()
    return plan if plan in PLAN_VIDEO_CREDITS else "pro"


def video_credits() -> dict[str, int]:
    """Per-clip credits for the models available on the configured plan."""
    return PLAN_VIDEO_CREDITS[flow_plan()]


def video_cost(model: str, count: int = 1) -> int:
    credits = video_credits()
    if model not in credits:
        raise ValueError(
            f"Flow video model {model!r} is not available on the {flow_plan()!r} plan "
            f"(FLOW_PLAN); available: {sorted(credits)}"
        )
    return credits[model] * max(1, int(count))


def project_budget() -> int:
    try:
        return int(os.environ.get("FLOW_CREDIT_BUDGET_PER_PROJECT", DEFAULT_BUDGET))
    except ValueError:
        return DEFAULT_BUDGET


def ledger_path(project_dir: Optional[Path]) -> Path:
    if project_dir is not None:
        return Path(project_dir) / LEDGER_FILENAME
    return REPO_ROOT / "output" / LEDGER_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CreditLedger:
    def __init__(self, project_dir: Optional[Path] = None, budget: Optional[int] = None):
        self.path = ledger_path(project_dir)
        self._budget_override = budget

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = self._read()
                yield data
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.path)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self) -> dict[str, Any]:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            data = {"budget": project_budget(), "entries": []}
        if self._budget_override is not None:
            data["budget"] = self._budget_override
        return data

    @staticmethod
    def _committed(data: dict[str, Any]) -> int:
        return sum(
            e["credits"] for e in data["entries"] if e["status"] in ("reserved", "charged")
        )

    def summary(self) -> dict[str, int]:
        data = self._read()
        committed = self._committed(data)
        return {
            "budget": data["budget"],
            "committed": committed,
            "remaining": data["budget"] - committed,
        }

    def reserve(self, *, tool: str, model: str, credits: int, note: str = "") -> str:
        with self._locked() as data:
            committed = self._committed(data)
            if committed + credits > data["budget"]:
                raise FlowBudgetExceeded(
                    f"Flow credit budget exceeded: {committed} committed + {credits} "
                    f"for {model} > budget {data['budget']} ({self.path}). "
                    "Raise FLOW_CREDIT_BUDGET_PER_PROJECT only with the user's approval."
                )
            entry_id = uuid.uuid4().hex[:12]
            data["entries"].append({
                "id": entry_id,
                "tool": tool,
                "model": model,
                "credits": credits,
                "status": "reserved",
                "note": note[:200],
                "media_ids": [],
                "created_at": _now(),
            })
            return entry_id

    def settle(self, entry_id: str, *, charged: bool, media_ids: Optional[list[str]] = None) -> None:
        with self._locked() as data:
            for entry in data["entries"]:
                if entry["id"] == entry_id:
                    entry["status"] = "charged" if charged else "refunded"
                    entry["media_ids"] = list(media_ids or [])
                    entry["settled_at"] = _now()
                    return
        raise KeyError(entry_id)
