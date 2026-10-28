"""The unattended morning digest: Claude Code headless runs the news agent so the film is in the owner's bot by 9:00.

    .venv/bin/python addons/news/morning.py [--city russia] [--deadline 08:45]

Started by news-morning-ru.timer at 06:00 MSK with /opt/openmontage/.env and /opt/watermark-cleaner/deploy/.env, after
the night pre-clean (01:00–07:00) has cleaned the best candidates' footage. Same launch as the factory worker
(factory/runner.py): `claude -p`, acceptEdits, Bash/Read/Write/Edit/Glob/Grep, Anthropic traffic through
FACTORY_CLAUDE_PROXY. The agent's last line is MORNING_RESULT; anything else (or the deadline) is reported to the
owner's Telegram so a missing morning film never goes unnoticed. Log: projects/news-<city>-<dd-mm>/morning.log.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MSK = timezone(timedelta(hours=3))
AGENTS = {"russia": "news-chp-russia", "moscow": "news-chp", "kazan": "news-chp-kazan", "spb": "news-chp-spb"}
SLUG = {"russia": "news-ru-{d}", "moscow": "news-vypusk-{d}", "kazan": "news-kzn-vypusk-{d}", "spb": "news-spb-vypusk-{d}"}
RESULT = re.compile(r"^MORNING_RESULT:\s*(READY|FAILED)\s*(.*)$", re.M)


def telegram(text: str) -> None:
    subprocess.run([str(REPO / ".venv" / "bin" / "python"), "addons/mural/send_telegram.py", text], cwd=REPO,
                   env={**os.environ, "PYTHONPATH": str(REPO)}, check=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="russia", choices=sorted(AGENTS))
    ap.add_argument("--deadline", default="08:45", help="MSK; the agent is stopped at this time")
    a = ap.parse_args()
    now = datetime.now(MSK)
    h, m = map(int, a.deadline.split(":"))
    stop = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if stop <= now:
        stop += timedelta(days=1)
    slug = SLUG[a.city].format(d=now.strftime("%d-%m"))
    proj = REPO / "projects" / slug
    proj.mkdir(parents=True, exist_ok=True)
    agent = (REPO / ".claude" / "agents" / f"{AGENTS[a.city]}.md").read_text(encoding="utf-8")
    prompt = "\n".join([
        "You are running OpenMontage unattended on the server (no human will answer questions).",
        f"Make today's digest as the agent below describes. Project: projects/{slug}. Today is {now:%Y-%m-%d}.",
        f"The film must be sent to the owner's Telegram and Kinescope before {a.deadline} MSK: keep the work lean — "
        "reuse the night pre-clean (clean_footage.py cuts from it), prefer post footage to illustrations, redraw a "
        "failed picture at most once.",
        "Server specifics: run python tools as the current user from /opt/openmontage with .venv/bin/python; t.me and "
        "OpenRouter need https_proxy=http://127.0.0.1:10809; the Flow browser is on DISPLAY=:99. Do not print secrets.",
        "End with exactly one line: MORNING_RESULT: READY projects/<slug> — or MORNING_RESULT: FAILED <reason>.",
        "", "AGENT INSTRUCTIONS:", agent])
    env = dict(os.environ)
    proxy = env.get("FACTORY_CLAUDE_PROXY", "")
    if proxy:  # Anthropic is unreachable from RU hosts; local and Google traffic stay direct
        env["HTTPS_PROXY"] = env["HTTP_PROXY"] = proxy
        env["NO_PROXY"] = ",".join(filter(None, [env.get("NO_PROXY"), "localhost", "127.0.0.1", ".google.com",
                                                 ".googleusercontent.com", ".gstatic.com", ".googleapis.com"]))
    cmd = [env.get("FACTORY_CLAUDE_BIN", "claude"), "-p", prompt, "--permission-mode", "acceptEdits",
           "--allowedTools", "Bash Read Write Edit Glob Grep", "--max-turns", "400", "--output-format", "text"]
    try:
        p = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=max(60, (stop - now).total_seconds()))
        out = (p.stdout or "") + ("\n[stderr]\n" + p.stderr if p.stderr else "")
    except subprocess.TimeoutExpired as e:
        out = f"{e.stdout or ''}\n[stopped at the {a.deadline} deadline]"
    (proj / "morning.log").write_text(f"PROMPT:\n{prompt}\n\nOUTPUT:\n{out}", encoding="utf-8")
    found = RESULT.findall(out)
    status, detail = found[-1] if found else ("FAILED", "no MORNING_RESULT line: " + out.strip()[-200:])
    print(f"MORNING_RESULT: {status} {detail}", flush=True)
    if status != "READY":
        telegram(f"⚠️ Утренний выпуск «{AGENTS[a.city]}» не собрался: {detail[:500]}\nЛог: projects/{slug}/morning.log")
        sys.exit(1)


if __name__ == "__main__":
    main()
