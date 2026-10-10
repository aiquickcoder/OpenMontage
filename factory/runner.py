"""Run one brief through the cartoon-ad pipeline with Claude Code in headless mode."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from factory.config import Settings

RESULT_LINE = re.compile(r"^AUTOPILOT_RESULT:\s*(READY|BUDGET|FAILED)\s+(\S+)(?:\s+(.*))?$", re.M)
ALLOWED_TOOLS = "Bash Read Write Edit Glob Grep"


@dataclass
class RunOutcome:
    status: str          # READY | BUDGET | FAILED
    project_dir: Path
    detail: str
    returncode: int


def build_prompt(brief: dict[str, Any]) -> str:
    opts = brief.get("options") or {}
    lines = [
        "You are running OpenMontage unattended (no human will answer questions).",
        "1. Read AGENT_GUIDE.md and follow its routing for the `cartoon-ad` pipeline.",
        "2. Read addons/autopilot/README.md and follow it exactly — it defines the approval "
        "policy, credit rules and the required final AUTOPILOT_RESULT line.",
        f"3. Use project id `{brief['id']}` (projects/{brief['id']}).",
    ]
    bait = opts.get("comment_bait")
    if bait:
        lines.append(f"4. Apply addons/comment-bait/README.md with mode `{bait}`.")
    for key in ("product", "duration", "audience", "style"):
        if opts.get(key):
            lines.append(f"- {key}: {opts[key]}")
    lines += ["", "BRIEF (from the owner):", brief["text"].strip()]
    return "\n".join(lines)


def build_command(settings: Settings, prompt: str) -> list[str]:
    return [
        settings.claude_bin, "-p", prompt,
        "--permission-mode", "acceptEdits",
        "--allowedTools", ALLOWED_TOOLS,
        "--max-turns", str(settings.claude_max_turns),
        "--output-format", "text",
    ]


def build_env(settings: Settings, base: Optional[dict[str, str]] = None) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    env.update({"FLOW_DRIVER": env.get("FLOW_DRIVER", "agent"), "FLOW_BRIDGE": env.get("FLOW_BRIDGE", "1")})
    if settings.claude_proxy:
        # Anthropic API is unreachable from RU hosts; Google and local traffic stay direct.
        env["HTTPS_PROXY"] = env["HTTP_PROXY"] = settings.claude_proxy
        env["NO_PROXY"] = ",".join(filter(None, [env.get("NO_PROXY"), "localhost", "127.0.0.1",
                                                 ".google.com", ".google", ".googleusercontent.com",
                                                 ".gstatic.com", ".googleapis.com"]))
    return env


def parse_result(output: str, default_project: Path) -> tuple[str, Path, str]:
    matches = RESULT_LINE.findall(output or "")
    if not matches:
        tail = (output or "").strip().splitlines()[-1:] or ["no output"]
        return "FAILED", default_project, f"no AUTOPILOT_RESULT line; last output: {tail[0][:200]}"
    status, project, detail = matches[-1]
    return status, Path(project), detail.strip()


def run_brief(settings: Settings, brief: dict[str, Any], runner=subprocess.run) -> RunOutcome:
    project_dir = settings.projects_dir / brief["id"]
    project_dir.mkdir(parents=True, exist_ok=True)
    log_path = project_dir / "factory_run.log"
    prompt = build_prompt(brief)
    try:
        proc = runner(build_command(settings, prompt), cwd=settings.repo, env=build_env(settings),
                      capture_output=True, text=True, timeout=settings.run_timeout_s)
        output, code = (proc.stdout or "") + ("\n[stderr]\n" + proc.stderr if proc.stderr else ""), proc.returncode
    except subprocess.TimeoutExpired as exc:
        output = f"{exc.stdout or ''}\n[timeout after {settings.run_timeout_s}s]"
        code = -1
    log_path.write_text(f"PROMPT:\n{prompt}\n\nOUTPUT:\n{output}", encoding="utf-8")
    status, reported, detail = parse_result(proc.stdout if code != -1 else output, project_dir)
    if not reported.is_absolute():
        reported = settings.repo / reported
    if status == "READY" and code != 0:
        status, detail = "FAILED", f"claude exited {code} after reporting READY"
    return RunOutcome(status=status, project_dir=reported, detail=detail, returncode=code)
