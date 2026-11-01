"""Which coding agent runs the unattended jobs (news autopilot, factory briefs): Codex or Claude Code.

Codex is the default since 2026-11-01 (owner: «переключить с ИИ Claude на Codex GPT … при построении видео, при работе
с новостями»; Claude's account on this server answers 403). Codex goes to the local codex-gateway (OpenAI Responses →
the ChatGPT subscription backend), which reaches chatgpt.com through the VPN proxy 127.0.0.1:10809 (owner: «он должен
через vpn ходить»). The gateway's secret comes from /etc/codex-gateway.env (EnvironmentFile of the units), never from
argv. FACTORY_AGENT=claude brings back the old launch.

Codex runs in its workspace-write sandbox with network on: it writes the repo, /tmp, the factory home (Telegram
session, render caches) and /srv/factory (news library, clean cache) — not a bypass of the sandbox.
"""
from __future__ import annotations

from pathlib import Path

GATEWAY = "http://172.22.0.1:8282/v1"
MODEL, EFFORT = "gpt-6.1-sol", "high"  # owner 2026-11-01: «gpt-6.1-sol … сделай high»
WRITABLE = ("/tmp", "/home/factory", "/srv/factory")


def engine(env: dict[str, str]) -> str:
    return env.get("FACTORY_AGENT", "codex")


def codex_command(prompt: str, cwd: Path, last_message: Path, env: dict[str, str], search: bool = False) -> list[str]:
    provider = ('{name="Quillon codex-gateway", base_url="' + env.get("FACTORY_CODEX_GATEWAY", GATEWAY)
                + '", env_key="CODEX_GW_SECRET", wire_api="responses"}')
    cmd = ["codex", "exec", "--skip-git-repo-check", "-C", str(cwd), "-s", "workspace-write",
           "-c", "sandbox_workspace_write.network_access=true",
           "-c", "model_provider=quillon_gw", "-c", f"model_providers.quillon_gw={provider}",
           "-m", env.get("FACTORY_CODEX_MODEL", MODEL),
           "-c", f'model_reasoning_effort="{env.get("FACTORY_CODEX_EFFORT", EFFORT)}"',
           "-o", str(last_message)]
    for d in WRITABLE:
        if Path(d).is_dir():
            cmd += ["--add-dir", d]
    return cmd + (["--search"] if search else []) + [prompt]


def codex_env(env: dict[str, str]) -> dict[str, str]:
    """The gateway is local: keep it off whatever HTTPS_PROXY the agent's tools need."""
    env = dict(env)
    env["NO_PROXY"] = ",".join(filter(None, [env.get("NO_PROXY"), "172.22.0.1"]))
    return env


def result_text(stdout: str, last_message: Path) -> str:
    """Where to look for the agent's RESULT line: Codex echoes the prompt (which spells the line out) into stdout,
    so only its last message counts."""
    return last_message.read_text(encoding="utf-8", errors="replace") if last_message.is_file() else ""
