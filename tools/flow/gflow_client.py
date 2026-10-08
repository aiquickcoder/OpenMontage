"""Thin subprocess wrapper around the pinned ``gflow`` CLI.

gflow drives a real (visible) Chrome profile logged into Google Flow, so:

- only one gflow process may touch the profile at a time (file lock);
- calls are spaced out to stay under Flow's anti-bot WAF (FLOW_MIN_INTERVAL_SEC);
- paid calls are never retried blindly — a failure after submit may already
  have spent credits, and gflow itself refuses to resubmit for the same reason.

Every command is run with ``--json`` and parsed against gflow's stable
machine-readable contract (gflow_cli/json_output.py).
"""

from __future__ import annotations

import fcntl
import json
import os
import random
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

PINNED_VERSION = "0.83.1"

DEFAULT_TIMEOUT_SECONDS = 15 * 60
STATE_DIR = Path(os.environ.get("FLOW_STATE_DIR") or Path.home() / ".cache" / "openmontage-flow")

# gflow exit codes that are raised BEFORE anything is submitted to Flow, so no
# credits can have been spent (gflow_cli/errors.py EXIT_CODE_MAP).
PRE_SUBMIT_EXIT_CODES = frozenset({
    2,   # click usage error
    3,   # auth expired
    8,   # auth missing
    10,  # WAF 403 (request rejected)
    11,  # configuration / unsupported option for this account
    12,  # login timeout
    13,  # security
    14,  # auth browser rejected
    17,  # model/mode incompatible
    18,  # video model selection
    24,  # browser engine unavailable
    27,  # upload rejected
    28,  # UI mode unavailable
    36,  # account migrated to flow.google.com, form unsupported
    37,  # insufficient credits
    38,  # account chooser stall
    39,  # Flow unavailable
})

EXIT_HINTS = {
    3: "Flow session expired — run `gflow auth login --browser chrome`.",
    8: "No Flow session — run `gflow auth login --browser chrome`.",
    10: "Flow anti-bot (WAF 403). Wait 30-60 min, then raise FLOW_MIN_INTERVAL_SEC.",
    4: "Flow rate limit / quota hit. Wait and retry later.",
    5: "Blocked by Flow content policy — rephrase the prompt.",
    23: "Flow UI changed and gflow could not find a control — run `gflow update` after checking release notes.",
    37: "Not enough Flow credits on the subscription this month.",
    39: "Flow is unavailable for this account/region — check VPN.",
}


class GflowError(RuntimeError):
    """A gflow command failed. ``charged`` is False only when provably pre-submit."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int,
        error_class: str = "",
        retryable: bool = False,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.error_class = error_class
        self.retryable = retryable
        self.payload = payload or {}

    @property
    def charged(self) -> bool:
        return self.exit_code not in PRE_SUBMIT_EXIT_CODES


@dataclass
class GflowRun:
    payload: dict[str, Any]
    stdout: str
    stderr: str
    duration_seconds: float


def gflow_bin() -> Optional[str]:
    explicit = os.environ.get("GFLOW_BIN")
    if explicit:
        return explicit if Path(explicit).exists() else None
    found = shutil.which("gflow")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "gflow"
    return str(fallback) if fallback.exists() else None


def is_installed() -> bool:
    return gflow_bin() is not None


def _min_interval() -> float:
    try:
        return float(os.environ.get("FLOW_MIN_INTERVAL_SEC", "60"))
    except ValueError:
        return 60.0


@contextmanager
def _profile_lock() -> Iterator[None]:
    """Serialize gflow runs and enforce spacing between consecutive calls."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = STATE_DIR / "gflow.lock"
    stamp_path = STATE_DIR / "last_call"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                last = float(stamp_path.read_text().strip())
            except (OSError, ValueError):
                last = 0.0
            interval = _min_interval()
            # Jitter so call spacing does not look machine-regular.
            wait = last + interval * random.uniform(1.0, 1.5) - time.time()
            if wait > 0 and interval > 0:
                time.sleep(wait)
            yield
        finally:
            stamp_path.write_text(str(time.time()))
            fcntl.flock(lock, fcntl.LOCK_UN)


def _parse_json(stdout: str) -> Optional[dict[str, Any]]:
    text = stdout.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    # Tolerate stray log lines before the JSON document.
    start = text.find("{")
    if start == -1:
        return None
    try:
        parsed = json.loads(text[start:])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def run(
    args: list[str],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    json_flag: bool = True,
    pace: bool = True,
) -> GflowRun:
    """Run ``gflow <args> [--json]`` and return the parsed payload.

    Raises GflowError on a non-zero exit or a ``status: fail`` payload.
    """
    binary = gflow_bin()
    if binary is None:
        raise GflowError(
            "gflow is not installed. Run: uv tool install gflow-cli==" + PINNED_VERSION,
            exit_code=127,
        )
    cmd = [binary, *args]
    if json_flag:
        cmd.append("--json")

    lock = _profile_lock() if pace else _no_lock()
    with lock:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            # The browser may still have submitted the job — treat as charged.
            raise GflowError(
                f"gflow timed out after {timeout}s: {' '.join(args[:2])}",
                exit_code=124,
            ) from exc
        duration = time.monotonic() - started

    payload = _parse_json(proc.stdout) if json_flag else None
    if proc.returncode != 0 or (payload and payload.get("status") == "fail"):
        err = (payload or {}).get("error") or {}
        exit_code = int(err.get("exit_code") or proc.returncode or 1)
        detail = (
            err.get("detail")
            or err.get("title")
            or (payload or {}).get("error_message")
            or proc.stderr.strip()[-800:]
            or proc.stdout.strip()[-800:]
            or "unknown error"
        )
        hint = err.get("remediation_hint") or EXIT_HINTS.get(exit_code, "")
        message = f"gflow {' '.join(args[:2])} failed (exit {exit_code}): {detail}"
        if hint:
            message += f"\nHint: {hint}"
        raise GflowError(
            message,
            exit_code=exit_code,
            error_class=str(err.get("class") or ""),
            retryable=bool(err.get("retryable")),
            payload=payload,
        )
    return GflowRun(
        payload=payload or {},
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_seconds=duration,
    )


@contextmanager
def _no_lock() -> Iterator[None]:
    yield


def installed_version() -> Optional[str]:
    binary = gflow_bin()
    if binary is None:
        return None
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip().rsplit(" ", 1)[-1] or None


def credit_balance() -> dict[str, Any]:
    """Current Flow credit balance (opens the browser profile; free)."""
    return run(["credits", "user"]).payload
