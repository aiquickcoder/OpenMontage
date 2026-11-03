"""The unattended run: Claude Code headless runs the news agent so the films are in the owner's bot on time — since
2026-10-30 two one-story films a run (projects/<slug>-1, -2), no digest.

    .venv/bin/python addons/news/morning.py [--city russia] [--deadline 08:45]      # today's two films
    .venv/bin/python addons/news/morning.py --city world [--deadline 13:30]          # the day's big story abroad
    .venv/bin/python addons/news/morning.py --project projects/<slug> [--deadline HH:MM]  # finish a started one
    .venv/bin/python addons/news/morning.py --viral --slot 1..4 [--account a|b] [--films 1|2] [--deadline HH:MM]
        # a TikTok film «почему это произошло» (owner 2026-11-01): viral.py picks for the slot's account, the agent
        # news-viral makes projects/news-viral-<account>-<dd-mm>-<n>; one viral run at a time (a lock), in turn

Viral timers since 2026-11-01 (MSK, README «Вирусный отбор для TikTok»; deploy/news/): news-viral-1 03:30, -2 05:00,
-3 15:30, -4 17:30 → news-viral@<slot>.service, the deadlines in tiktok_accounts.json "slots" — slots 1 and 3 are the
day's first account's films, 2 and 4 the second's; the city and Russia timers below are switched off (unit files kept).

Timers (all MSK): news-morning-ru 06:00 (in the bot by 9:00); news-evening-kazan 17:30, -moscow 17:50, -spb 18:10
(in the bot by 20:45, the best evening window by the 14-day channel stats). Started with /opt/openmontage/.env and /opt/watermark-cleaner/deploy/.env, after
the night pre-clean (01:00–07:00) has cleaned the best candidates' footage. Same launch as the factory worker
(factory/runner.py): Codex through the local codex-gateway and the VPN (factory/agent_cli.py; FACTORY_AGENT=claude
— the old `claude -p`, acceptEdits, Anthropic traffic through FACTORY_CLAUDE_PROXY). The agent's last line is MORNING_RESULT; anything else (or the deadline) is reported to the
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
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "addons" / "news"))  # viral.py, viral_stats.py
from factory import agent_cli  # noqa: E402  (Codex by default since 2026-11-01, FACTORY_AGENT=claude for the old run)
MSK = timezone(timedelta(hours=3))
AGENTS = {"russia": "news-chp-russia", "moscow": "news-chp", "kazan": "news-chp-kazan", "spb": "news-chp-spb",
          "world": "news-world", "viral": "news-viral"}
SLUG = {"russia": "news-ru-{d}", "moscow": "news-msk-{d}", "kazan": "news-kzn-{d}", "spb": "news-spb-{d}",
        "world": "news-world-{d}"}
VIDEOS = 2  # one story, one film (owner 2026-10-30: «1 ролик на 1 новость», «2 лучших новости» a run)
# the day's big story abroad (owner 2026-10-31, after Poland: «далее нужно нам начать собирать такие ролики»): one
# connected 40–70 s film a run, its facts checked on the web — the only agent here with WebSearch / WebFetch
WORLD_TASK = ("Make today's big-story film (README «Большая история», the agent instructions below): run world.py "
              "--hours 24, take the biggest story abroad (#1 unless there is a reason — say it), build its dossier "
              "with dossier.py (--fetch, --yt in the country's language), check every figure and quote in the "
              "country's own media and the agencies (WebSearch / WebFetch, two sources each), write story.json "
              "(\"kind\": \"world\", \"country\") and caption.txt, voice it (build.py --voice-only), one shot per "
              "phrase from our footage, clean_footage.py --plan + media/wm.json, then produce.py --send --kinescope. "
              "Project: projects/{film}.")
# a viral TikTok film (owner 2026-11-01: «вирусные залетающие новости для тик тока»; «4 ролика, по 2 на аккаунт»)
VIRAL_TASK = ("Make {n} TikTok film(s) for the account «{name}» (key {key}) in the format «Почему это произошло» "
              "(README «Вирусный отбор для TikTok», the agent instructions below): viral.py has already picked the "
              "candidates for this slot — the list is below and in projects/{first}/viral.json (its posts, signals, "
              "the AI's hook / why / question drafts — drafts, not facts). Take #1{also} unless it cannot be told "
              "honestly or has no real frames (say why, then the next). Build a dossier (dossier.py / tg_client.py "
              "search over all our channels), check the facts and the cause («почему») in two sources each "
              "(WebSearch / WebFetch), write story.json (\"kind\": \"viral\", \"viral\": {{\"account\": \"{key}\", "
              "\"slot\": {slot}, \"key\": <the candidate's key>}}, source.key = the candidate's key, source.links = its "
              "posts) and caption.txt, voice it (build.py --voice-only), one shot per phrase from real frames, "
              "clean_footage.py --plan + media/wm.json, then produce.py --send --kinescope. Projects: {films}.")
RESULT = re.compile(r"^MORNING_RESULT:\s*(READY|FAILED)\s*(.*)$", re.M)
VIRAL_LOCK = Path("/tmp/news_viral.lock")  # one viral run at a time: the next slot waits for the one before


def telegram(text: str) -> None:
    subprocess.run([str(REPO / ".venv" / "bin" / "python"), "addons/mural/send_telegram.py", text], cwd=REPO,
                   env={**os.environ, "PYTHONPATH": str(REPO)}, check=False)


def films_in_work(exclude: set[str], idle_min: float = 15) -> list[str]:
    """Other films an agent is making right now: a project whose story.json, assets, logs or renders changed in the
    last idle_min minutes and that is not delivered since (kinescope.json older than that)."""
    now, out = time.time(), []
    for d in (REPO / "projects").glob("news-*"):
        if d.name in exclude or not (d / "story.json").is_file():
            continue
        files = [d / "story.json", d / "events.jsonl", *d.glob("assets/*"), *d.glob("logs/*"), *d.glob("renders/*")]
        latest = max((f.stat().st_mtime for f in files if f.is_file()), default=0)
        k = d / "kinescope.json"
        if now - latest < idle_min * 60 and not (k.is_file() and k.stat().st_mtime >= latest - 120):
            out.append(d.name)
    return out


def delivered_all(films: list[str]) -> bool:
    return all((REPO / "projects" / f / "kinescope.json").is_file() for f in films)


def viral_turn(stop: datetime, film: str, films: list[str] | None = None):
    """Wait for our turn (owner 2026-11-01: one heavy job at a time on this server): the viral lock (the slot before
    may still be at work), then for another film to be done — its produce.py, render or cleaning, or an agent still
    making it (its project changing; the Frankfurt film, 02.11). Past an hour before the deadline there is no time
    left for a film — tell the owner and stop."""
    import fcntl

    import produce

    def late(why: str) -> None:
        if delivered_all(films or [film]):  # made by hand meanwhile (the owner's night run, 02.11) — nothing to do
            print("MORNING_RESULT: READY (the slot's films are out already)", flush=True)
            sys.exit(0)
        if datetime.now(MSK) >= stop - timedelta(hours=1):
            telegram(f"⚠️ Вирусный ролик {film} не начат: {why}, до срока меньше часа")
            sys.exit(1)

    f = VIRAL_LOCK.open("w")
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            late("предыдущий слот работает до сих пор")
            time.sleep(60)
    told = False
    while jobs := produce.heavy_jobs() or films_in_work(set(films or [film])):
        if not told:
            print("waiting for another film: " + "; ".join(jobs[:3]), flush=True)
            told = True
        late("на сервере всё ещё собирается другой ролик")
        time.sleep(60)
    return f


def viral_pick(slot: int, acc: dict, films: list[str], proj: Path) -> str:
    """viral.py pick for this slot's account: the candidates (stdout, for the prompt) and viral.json; the #1 is
    claimed for the first film, the #2 for a second one, so the other account's slots never take them."""
    import viral

    cmd = [str(REPO / ".venv" / "bin" / "python"), "addons/news/viral.py", "pick", "--slot", str(slot),
           "--account", acc["key"], "--json", str(proj / "viral.json"), "--claim", films[0], "--top", "6"]
    env = {**os.environ, "https_proxy": os.environ.get("NEWS_HTTPS_PROXY", "http://127.0.0.1:10809")}
    env.pop("HTTPS_PROXY", None)
    p = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=1800)
    (proj / "viral_pick.log").write_text(p.stdout + "\n[stderr]\n" + p.stderr, encoding="utf-8")
    try:
        cands = json.loads((proj / "viral.json").read_text(encoding="utf-8"))["candidates"]
    except (OSError, ValueError, KeyError):
        cands = []
    if p.returncode != 0 or not cands:
        telegram(f"⚠️ Вирусный отбор для {films[0]} не дал кандидатов: {(p.stderr or p.stdout).strip()[-300:]}")
        sys.exit(1)
    for film, cand in zip(films[1:], cands[1:]):
        viral.save_claim(viral.claim_of(film, acc, cand, cand.get("sig") or {}))
    return p.stdout.strip()[-12000:]


def viral_done(delivered: list[str]) -> None:
    """A delivered film: its claim names the story it was made of, and it goes into the TikTok numbers store."""
    import viral
    import viral_stats

    for f in delivered:
        try:
            viral.claim_made(f, REPO / "projects" / f)
            viral_stats.register(viral_stats.db(), REPO / "projects" / f)
        except Exception as e:  # noqa: BLE001 — the film is out; a bookkeeping slip is only logged
            print(f"viral bookkeeping {f}: {e}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="russia", choices=sorted(AGENTS))
    ap.add_argument("--deadline", help="MSK; the agent is stopped at this time (08:45; --viral: the slot's deadline "
                                       "in tiktok_accounts.json)")
    ap.add_argument("--project", type=Path, help="finish this started digest (story.json, maybe its voice) instead")
    ap.add_argument("--slug", help="a new digest under this name (an extra one in a day: the dated name is taken)")
    ap.add_argument("--note", help="the owner's remarks on this digest, passed to the agent as they are")
    ap.add_argument("--viral", action="store_true", help="a TikTok film of the viral pick (README «Вирусный отбор»)")
    ap.add_argument("--slot", type=int, default=1, help="--viral: the day's slot 1–4 (1, 3 the first account's)")
    ap.add_argument("--account", help="--viral: this account (tiktok_accounts.json key) instead of the slot's")
    ap.add_argument("--films", type=int, default=1, help="--viral: films this run (2 = both of an account's films)")
    a = ap.parse_args()
    if a.viral:
        a.city = "viral"
        if not a.deadline:
            import viral

            a.deadline = next((x["deadline"] for x in viral.slots() if x["slot"] == a.slot), "21:30")
    a.deadline = a.deadline or "08:45"
    now = datetime.now(MSK)
    if a.project:
        a.city = json.loads((REPO / a.project / "story.json").read_text(encoding="utf-8")).get("city", a.city)
        if a.project.name.startswith("news-viral-"):  # a viral film finished by hand: its own agent
            a.city = "viral"
    h, m = map(int, a.deadline.split(":"))
    stop = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if stop <= now:
        stop += timedelta(days=1)
    acc, picked, lock = None, "", None
    if a.viral and not a.project:
        import viral

        acc = viral.account(a.account) if a.account else viral.slot_account(a.slot, now.date())
        first = viral.film_number(a.slot)
        slug = f"news-viral-{acc['key']}-{now:%d-%m}"
        films = [f"{slug}-{n}" for n in range(first, first + a.films)]
        films = [f for f in films if not (REPO / "projects" / f / "kinescope.json").is_file()]
        if not films:
            print("MORNING_RESULT: READY (the slot's films are out already)", flush=True)
            return
        lock = viral_turn(stop, films[0], films)
        films = [f for f in films if not (REPO / "projects" / f / "kinescope.json").is_file()]
        if not films:  # another run made them while this one waited for its turn — never a second copy
            print("MORNING_RESULT: READY (the slot's films are out already)", flush=True)
            return
        proj = REPO / "projects" / films[0]
        proj.mkdir(parents=True, exist_ok=True)
        picked = viral_pick(a.slot, acc, films, proj)
    else:
        slug = a.project.name if a.project else a.slug or SLUG[a.city].format(
            d=now.strftime("%d-%m") + ("" if now.hour < 12 else "-vecher"))
        videos = 1 if a.city == "world" else VIDEOS
        films = [slug] if a.project else [f"{slug}-{n}" for n in range(1, videos + 1)]
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
         if a.project else WORLD_TASK.format(film=films[0]) if a.city == "world" else
         VIRAL_TASK.format(n=len(films), name=acc["name"], key=acc["key"], first=films[0], slot=a.slot,
                           also=" (and #2 for the second film)" if len(films) > 1 else "",
                           films=", ".join(f"projects/{f}" for f in films))
         + f" The card tag: {acc['card_tag']!r} ({{place}} = РОССИЯ, the country or the city in capitals); the closing "
           f"line: «Как думаете, <the question>? {acc['cta']}»; hashtags: {' '.join(acc['hashtags'])}."
         + "\n\nTHE PICK (viral.py):\n" + picked
         if a.city == "viral" else
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
        "a map of the place; when these are thin (a story abroad, a viral one), short pieces of the event itself from "
        "TikTok — yt_footage.py tt-search / tt-peek / tt-cut, README «Кадры из TikTok»: ≤ 4 s each, ≤ 3 a film, never "
        "the first shot, no credit. Between the facts and the question, 1–2 phrases «почему так вышло» that answer the "
        "story's main question (owner 2026-11-03, README «Почему так вышло»): politics, accusations, disputes from the "
        "sources with the author; plain uncontested common knowledge without a reference; story.json \"why\" + "
        "\"why_basis\"; neither → \"why_skip\", never a guess. Shots by score (owner 2026-11-03, README «Оценка кадров»): every shot gets a "
        "\"role\" (event / quote / key / context / map) and your \"score\" 0–10 by the rubric, picked from several "
        "candidates; a still only as a quote (a post, a tweet, a headline, an article's text, a statement), a key "
        "picture of the story itself or one map — anything around the story is video from our engine, cleaned; no "
        "repeats at all. produce.py runs shot_judge.py before the render: on «shots: …» replace those shots and run "
        "it again. "
        "Video runs whole (one shot per continuous piece); stills move (pan / push-in, by "
        "themselves). "
        + ("A top story with no footage of its own is told with thematic real footage you plan phrase by phrase "
           "(README «Новость без своих кадров»: yt_footage.py search / peek / find / cut) — when even that is thin, "
           "take the next candidate. " if a.city == "viral" else
           "A story without enough real frames is not taken — take the next one. ")
        + "Every film opens on its "
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
    last = proj / "morning.last.txt"
    last.unlink(missing_ok=True)
    codex = agent_cli.engine(env) == "codex"
    if codex:
        cmd, env = (agent_cli.codex_command(prompt, REPO, last, env, search=a.city in ("world", "viral")),
                    agent_cli.codex_env(env))
    else:
        tools = "Bash Read Write Edit Glob Grep" + (" WebSearch WebFetch" if a.city in ("world", "viral") else "")
        cmd = [env.get("FACTORY_CLAUDE_BIN", "claude"), "-p", prompt, "--permission-mode", "acceptEdits",
               "--allowedTools", tools, "--max-turns", "400", "--output-format", "text"]
    started = time.time()
    try:
        p = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=max(60, (stop - now).total_seconds()))
        out = (p.stdout or "") + ("\n[stderr]\n" + p.stderr if p.stderr else "")
    except subprocess.TimeoutExpired as e:
        out = f"{e.stdout or ''}\n[stopped at the {a.deadline} deadline]"
    (proj / "morning.log").write_text(f"PROMPT:\n{prompt}\n\nOUTPUT:\n{out}", encoding="utf-8")
    found = RESULT.findall(agent_cli.result_text(out, last) if codex else out)
    delivered = [f for f in films if (REPO / "projects" / f / "kinescope.json").is_file()
                 and (REPO / "projects" / f / "kinescope.json").stat().st_mtime >= started]
    if not found and delivered:
        # text output keeps only the agent's last message; one that said its result earlier and then «Task complete»
        # was reported as FAILED although the film was out (Moscow 29.10). The film on Kinescope is the proof.
        found = [("READY", " ".join(f"projects/{f}" for f in delivered) + " (delivered; no result line)")]
    status, detail = found[-1] if found else ("FAILED", "no MORNING_RESULT line: " + out.strip()[-200:])
    if a.city == "viral":
        viral_done(delivered)
    print(f"MORNING_RESULT: {status} {detail}", flush=True)
    if status != "READY":
        telegram(f"⚠️ Ролики «{AGENTS[a.city]}» не собрались: {detail[:500]}\nЛог: projects/{films[0]}/morning.log")
        sys.exit(1)


if __name__ == "__main__":
    main()
