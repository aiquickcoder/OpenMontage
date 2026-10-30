"""The unattended run: Claude Code headless runs the news agent so the films are in the owner's bot on time — since
2026-10-30 two one-story films a run (projects/<slug>-1, -2), no digest.

    .venv/bin/python addons/news/morning.py [--city russia] [--deadline 08:45]      # today's two films
    .venv/bin/python addons/news/morning.py --project projects/<slug> [--deadline HH:MM]  # finish a started one

Timers (all MSK): news-morning-ru 06:00 (in the bot by 9:00); news-evening-kazan 17:30, -moscow 17:50, -spb 18:10
(in the bot by 20:45, the best evening window by the 14-day channel stats). Started with /opt/openmontage/.env and /opt/watermark-cleaner/deploy/.env, after
the night pre-clean (01:00–07:00) has cleaned the best candidates' footage. Same launch as the factory worker
(factory/runner.py): `claude -p`, acceptEdits, Bash/Read/Write/Edit/Glob/Grep, Anthropic traffic through
FACTORY_CLAUDE_PROXY. The agent's last line is MORNING_RESULT; anything else (or the deadline) is reported to the
owner's Telegram so a missing morning film never goes unnoticed. Log: projects/news-<city>-<dd-mm>/morning.log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MSK = timezone(timedelta(hours=3))
AGENTS = {"russia": "news-chp-russia", "moscow": "news-chp", "kazan": "news-chp-kazan", "spb": "news-chp-spb"}
SLUG = {"russia": "news-ru-{d}", "moscow": "news-msk-{d}", "kazan": "news-kzn-{d}", "spb": "news-spb-{d}"}
VIDEOS = 2  # one story, one film (owner 2026-10-30: «1 ролик на 1 новость», «2 лучших новости» a run)
RESULT = re.compile(r"^MORNING_RESULT:\s*(READY|FAILED)\s*(.*)$", re.M)


def telegram(text: str) -> None:
    subprocess.run([str(REPO / ".venv" / "bin" / "python"), "addons/mural/send_telegram.py", text], cwd=REPO,
                   env={**os.environ, "PYTHONPATH": str(REPO)}, check=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="russia", choices=sorted(AGENTS))
    ap.add_argument("--deadline", default="08:45", help="MSK; the agent is stopped at this time")
    ap.add_argument("--project", type=Path, help="finish this started digest (story.json, maybe its voice) instead")
    ap.add_argument("--slug", help="a new digest under this name (an extra one in a day: the dated name is taken)")
    ap.add_argument("--note", help="the owner's remarks on this digest, passed to the agent as they are")
    a = ap.parse_args()
    now = datetime.now(MSK)
    if a.project:
        a.city = json.loads((REPO / a.project / "story.json").read_text(encoding="utf-8")).get("city", a.city)
    h, m = map(int, a.deadline.split(":"))
    stop = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if stop <= now:
        stop += timedelta(days=1)
    slug = a.project.name if a.project else a.slug or SLUG[a.city].format(
        d=now.strftime("%d-%m") + ("" if now.hour < 12 else "-vecher"))
    films = [slug] if a.project else [f"{slug}-{n}" for n in range(1, VIDEOS + 1)]
    if a.slug and any((REPO / "projects" / f / "story.json").is_file() for f in films):
        sys.exit(f"projects/{slug}-… exists: an extra run needs a new name")
    proj = REPO / "projects" / films[0]  # the run's log lives with its first film
    proj.mkdir(parents=True, exist_ok=True)
    agent = (REPO / ".claude" / "agents" / f"{AGENTS[a.city]}.md").read_text(encoding="utf-8")
    prompt = "\n".join([
        "You are running OpenMontage unattended on the server (no human will answer questions).",
        (f"Finish the started digest projects/{slug}: its story.json (texts, sources) is final — do not rewrite the "
         "texts; if assets/narration.mp3 exists the voice is done (use build.py --voice-only only to read the shot "
         "counts, it reuses nothing: run it only when there is no voice yet). Copy the stories' post media from the "
         "city base (candidates.json media_local by source.key) where media/ lacks it, set one shot per phrase that "
         "build.py --voice-only lists (assets/beats.json), write illustrations for what has no footage, run "
         "clean_footage.py --plan and open every qa/wm_*.jpg, give each NEEDS A LOOK file its media/wm.json "
         "decision, then produce.py."
         if a.project else
         f"Make today's {VIDEOS} films — one story each (owner 2026-10-30: one story, one film; no digest, no cat, "
         f"no AI pictures): the {VIDEOS} best stories pick.py offers that have real pictures, in the one-story "
         f"format of the README «Формат с 2026-10-30» (a hook, the story's own footage, a loop). Projects: "
         + ", ".join(f"projects/{f}" for f in films) + ". Finish each with produce.py --send --kinescope."),
        f"Today is {now:%Y-%m-%d}.",
        *([f"The owner's remarks on this digest (fix every one): {a.note}"] if a.note else []),
        f"The films must be in the owner's Telegram and on Kinescope before {a.deadline} MSK: keep the work lean — "
        "reuse the night pre-clean (clean_footage.py cuts from it). NO AI pictures at all (owner 2026-10-30; "
        "cities.json ai_max_share = 0, build.py refuses them): only real frames — the post's media of every channel "
        "(big Telegram videos come whole through tg_client.py), the agency's own video (sources.py search \"<event "
        "words>\" finds it on Rutube), a news site's article photo or video (article_video.py), the base of pictures "
        "(library.py find <city> <place>), a Commons photo of the very place (fetch_commons.py — it adds to the base), "
        "a map of the place. Video runs whole (one shot per continuous piece); stills move (pan / push-in, by "
        "themselves). A story without enough real frames is not taken — take the next one. Every film opens on its "
        "hook (story.json \"hook\": the strongest honest bait, README «Хук») and its hardest picture. Put every new "
        "real picture you used into the base (library.py add … --tags city,place,agency). Check build.py "
        "--storyboard before the render.",
        "Server specifics: run python tools as the current user from /opt/openmontage with .venv/bin/python; t.me and "
        "OpenRouter need https_proxy=http://127.0.0.1:10809. Do not print secrets.",
        "To wait for a background job, wait on its PID (`while kill -0 $PID`) or on a file it writes — never on "
        "`pgrep -f '<command text>'`: it matches your own waiting shell and never ends (two agents hung for hours on "
        "2026-10-29). Once produce.py has printed PRODUCE_RESULT, finish with the MORNING_RESULT line right away.",
        "End with exactly one line: MORNING_RESULT: READY " + " ".join(f"projects/{f}" for f in films)
        + " (only those delivered) — or MORNING_RESULT: FAILED <reason>.",
        "", "AGENT INSTRUCTIONS:", agent])
    env = dict(os.environ)
    proxy = env.get("FACTORY_CLAUDE_PROXY", "")
    if proxy:  # Anthropic is unreachable from RU hosts; local and Google traffic stay direct
        env["HTTPS_PROXY"] = env["HTTP_PROXY"] = proxy
        env["NO_PROXY"] = ",".join(filter(None, [env.get("NO_PROXY"), "localhost", "127.0.0.1", ".google.com",
                                                 ".googleusercontent.com", ".gstatic.com", ".googleapis.com"]))
    cmd = [env.get("FACTORY_CLAUDE_BIN", "claude"), "-p", prompt, "--permission-mode", "acceptEdits",
           "--allowedTools", "Bash Read Write Edit Glob Grep", "--max-turns", "400", "--output-format", "text"]
    started = time.time()
    try:
        p = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=max(60, (stop - now).total_seconds()))
        out = (p.stdout or "") + ("\n[stderr]\n" + p.stderr if p.stderr else "")
    except subprocess.TimeoutExpired as e:
        out = f"{e.stdout or ''}\n[stopped at the {a.deadline} deadline]"
    (proj / "morning.log").write_text(f"PROMPT:\n{prompt}\n\nOUTPUT:\n{out}", encoding="utf-8")
    found = RESULT.findall(out)
    delivered = [f for f in films if (REPO / "projects" / f / "kinescope.json").is_file()
                 and (REPO / "projects" / f / "kinescope.json").stat().st_mtime >= started]
    if not found and delivered:
        # text output keeps only the agent's last message; one that said its result earlier and then «Task complete»
        # was reported as FAILED although the film was out (Moscow 29.10). The film on Kinescope is the proof.
        found = [("READY", " ".join(f"projects/{f}" for f in delivered) + " (delivered; no result line)")]
    status, detail = found[-1] if found else ("FAILED", "no MORNING_RESULT line: " + out.strip()[-200:])
    print(f"MORNING_RESULT: {status} {detail}", flush=True)
    if status != "READY":
        telegram(f"⚠️ Ролики «{AGENTS[a.city]}» не собрались: {detail[:500]}\nЛог: projects/{films[0]}/morning.log")
        sys.exit(1)


if __name__ == "__main__":
    main()
