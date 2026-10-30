"""Stage 1 of the news workflow: find the city's fresh incidents and crimes and rank them for a «ЧП» short.

    python3 -I addons/news/pick.py [--city moscow|kazan|spb] [--base DIR] [--posts 150] [--hours 36] [--top 5] [--no-harvest] [--crime]
                                   [--fill-days 7] [--fill-top 10]

1. Runs harvest.py (Telegram previews -> <base>/base.json + media/), unless --no-harvest.
2. Drops what the format never covers (war / drones / politics, suicide, sexual violence, child victims), events that
   are not in Moscow, older than --hours, and events already made (source links/keys in projects/news-*/story.json).
3. Scores the rest: audience reactions under the post, footage (video > photo > none), how many channels carried
   it, freshness, and how much of a real incident it is (fire / knife / syringe / crash … vs. a curiosity).
   Crimes and above all detentions get extra weight — the format's focus since 2026-10-27; --crime keeps only them.
4. Writes <base>/candidates.json (ranked, with the reasons) and prints the top list.
5. «Добор» (owner 2026-10-28): incidents, arrests and detentions from the last --fill-days that no film has covered
   yet, ranked the same way, go to candidates.json["fill"] and print after the fresh list. When the fresh news is too
   thin for a digest (mornings, quiet days), the agent tops the digest up from there — with the event's own date.

The agent (or the owner) takes #1 unless there is a reason not to (say it); the facts still come only from the post.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import sources  # noqa: E402
import topics  # noqa: E402

# never covered (README «Какое событие брать»). Children: deaths and violence only — a child hurt in a crash or a
# fire is ordinary city news and stays (owner 2026-10-27, the five-car crash on проспект Универсиады)
SKIP = {
    "война/БПЛА/политика": r"\bВСУ\b|\bСВО\b|БПЛА|беспилот|дрон|ракет|воздушн\w* тревог|в укрыти|обстрел|атак\w* (?:на|ВСУ)|мобилиз|санкци\w* (?:против|ЕС|США|Запад)|антироссийск\w* санкци|вв[её]л\w* санкци|выбор(?!г)|депутат|госдум|в кремле|кремль (?:заяв|сообщ|ответ|прокоммент|назва)|песков|митинг|теракт|политолог|оппозиц|иноагент|госизмен|экстремист",
    "суицид": r"суицид|самоубий|покончил\w* с собой|свёл счёты|свел счеты",
    "сексуальное насилие": r"изнасил|домога|педофил|растлен|развращ|между ног|сексуальн\w* характер"
                           r"|проституц\w*.{0,120}несовершеннолет|несовершеннолет\w*.{0,120}проституц",  # 30.10
    "дети-жертвы": r"(?:ребён|ребен|младен|малыш|школьни|подрост|\d+-летн\w+ (?:мальчик|девочк))\w*.{0,60}(?:погиб|умер|скончал|утонул|насмерть)"
                  r"|(?:убий|убил|убит|зарез|задуш|расправ|напал|нападени)\w*.{0,60}?(?:ребён|ребен|младен|малыш|мальчик|девочк|школьни)",
}
SCAM = r"мошенн|афер|лжесотрудник|лжеполицейск"
SCAM_WAR = r"(?:на |для |в пользу )?(?:нужд\w* |финансирован\w* |помощ\w* )?ВСУ"
# how much of a real incident it is: strong words score, curiosities do not
CRIME_BONUS, DETENTION_BONUS = 1.0, 1.0  # the format's focus: crimes, and detentions above all
STRONG = r"пожар|(?<![а-яё])горит|загорел|взрыв|ДТП|авари|столкн|сбил|наезд|(?<![а-яё])нож|зарез|стрельб|выстрел|шприц|укол|напал|ограб|похит|обрушен|рухн|провал|затопи|прорыв|эвакуац|задержан|пропал\w* без вести|исчез"
CITIES = json.loads((HERE / "cities.json").read_text(encoding="utf-8"))
REACTION = re.compile(r"([\U0001F300-\U0001FAFF☀-➿]️?)\s*(\d+(?:[.,]\d+)?)([KkКк]?)")
TAIL = re.compile(r"(?:BAZA в Telegram|BAZA в MAX|Подпишись\S*|Прислать новость\S*)", re.I)


def reactions(text: str) -> int:
    total = 0
    for _, num, k in REACTION.findall(text):
        v = float(num.replace(",", "."))
        v = v * 1000 if k else min(v, 9999)  # glued numbers ("25840" = 258 + 40) are capped
        total += int(v)
    return total


def clean(text: str) -> str:
    text = REACTION.sub(" ", text)
    text = TAIL.sub(" ", text)
    text = re.sub(r"^[\W\d_]+", "", text)  # leading emoji
    return re.sub(r"\s+", " ", text).strip()


def made_already(root: Path) -> tuple[set[str], set[str]]:
    links, keys = set(), set()
    for f in glob.glob(str(root / "projects" / "news-*" / "story.json")):
        try:
            s = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for src in [s.get("source") or {}] + [x.get("source") or {} for x in s.get("stories") or []]:  # digests too
            links.update(src.get("links") or [])
            if src.get("key"):
                keys.add(src["key"])
    return links, keys


def age_hours(dt: str, now: datetime) -> float:
    try:
        t = datetime.fromisoformat(dt).replace(tzinfo=timezone.utc)  # t.me <time datetime> is UTC
    except ValueError:
        return 999.0
    return max(0.0, (now - t).total_seconds() / 3600)


def judge(e: dict, now: datetime, max_hours: float, done_links: set[str], done_keys: set[str],
          city: str = "moscow", crime_only: bool = False) -> dict:
    text = e.get("text", "")
    tags = topics.tags(text)
    note = ""
    # fraud scripts scare victims with «переводы для нужд ВСУ» — that is a scam story, not war news
    checked = re.sub(SCAM_WAR, " ", text, flags=re.I) if re.search(SCAM, text, re.I) else text
    why_not = [name for name, rx in SKIP.items() if re.search(rx, checked, re.I)
               # an agency's own release (НАК, МВД on Rutube): its sabotage and treason detentions are what the owner
               # wants (2026-10-30, «тут как раз то, что нужно»); the Telegram channels' war news stays out
               and not (e.get("official") and name == "война/БПЛА/политика")]
    c = CITIES[city]
    if c.get("skip_extra") and re.search(c["skip_extra"], text, re.I):  # a format's own exclusions (geopolitics, ads)
        why_not.append("не формат")
    if c.get("events_only", True) and not topics.is_event(text):  # the base may come from an older/own harvester
        why_not.append("не ЧП")
    if crime_only and "crime" not in tags:
        why_not.append("не криминал")
    chans = set(e.get("channels") or [])
    local = {h for h, whole in c["channels"] if whole} | {sources.channel(s) for s in c.get("sources", []) if s[2]}
    if not (chans & local) and not re.search(c["geo"], text, re.I):
        why_not.append(f'не {c["name"]}')
    age = age_hours(e.get("dt", ""), now)
    if age > max_hours:
        why_not.append(f"старое ({age:.0f} ч)")
    if e.get("key") in done_keys or set(e.get("links") or []) & done_links:
        why_not.append("уже сделано")
    got = e.get("media_got") or []
    media = 3.0 if "mp4" in got else (1.5 if "photo" in got else 0.0)
    video_s, photos = footage(e)
    visual = ("rich" if video_s >= 8 or photos >= 4 or (video_s >= 3 and photos >= 2) else
              "thin" if video_s or photos else "none")
    budget = c.get("ai_max_share", 1)  # AI pictures limited (owner 2026-10-29): stories with footage of their own first
    if budget == 0 and visual == "none":
        why_not.append("нет своих кадров (ИИ выключен)")
    if budget < 1:
        media += {"rich": 1.0, "thin": 0.0, "none": -1.5}[visual]
    strong = len(set(m.lower() for m in re.findall(STRONG, text, re.I)))
    react = reactions(text)
    crime = "crime" in tags
    if c.get("rank") == "hype":  # the top-of-Russia format: views x channels from harvest_top.py, freshness, media
        score = (math.log10(1 + (e.get("hype") or 0)) * 2 + media + 0.8 * (len(chans) - 1)
                 + 0.5 * crime - age / 12)
        if chans and chans <= {"topor", "toporlive"}:  # the owner's digest marks these «сверить факт»
            if {"crime", "detention", "fatal"} & set(tags):  # an accusation or a death needs a second source
                why_not.append("только Топор — нужен второй источник")
            else:
                note = "только Топор — сверить факт"
    else:
        score = (math.log10(1 + react) * 1.5 + media + 1.0 * (len(chans) - 1) + min(strong, 3) * 0.8
                 + CRIME_BONUS * crime + DETENTION_BONUS * ("detention" in tags)
                 - age / 12 - (2.0 if strong == 0 and not crime else 0.0))
    return {"key": e.get("key"), "dt": e.get("dt"), "age_h": round(age, 1), "score": round(score, 2), "note": note,
            "skip": why_not, "tags": tags, "reactions": react, "media": got, "channels": sorted(chans), "links": e.get("links"),
            "strong_words": strong, "text": clean(text), "video_s": round(video_s, 1), "photos": photos, "visual": visual,
            "media_local": [m.get("local") for m in e.get("media", []) if m.get("local")],
            "official": bool(e.get("official"))}


def footage(e: dict) -> tuple[float, int]:
    """Seconds of the story's own video and the number of its photos (downloaded media of every channel)."""
    secs, photos = 0.0, 0
    for m in e.get("media", []):
        f = m.get("local")
        if not f or not Path(f).is_file():
            continue
        if m.get("type") == "mp4":
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f],
                                 capture_output=True, text=True).stdout.strip()
            secs += float(out or 0)
        else:
            photos += 1
    return secs, photos


def describe(c: dict) -> str:
    parts = ([f"видео {c['video_s']:.0f} с"] if c.get("video_s") else []) + ([f"{c['photos']} фото"] if c.get("photos") else [])
    return (" + ".join(parts) or "без медиа") + (" ведомства, без очистки" if c.get("official") else "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="moscow", choices=sorted(CITIES))
    ap.add_argument("--base", default=os.environ.get("NEWS_BASE"), help="default: the city's server_base when it exists here, else its base")
    ap.add_argument("--posts", type=int, default=150)
    ap.add_argument("--hours", type=float, default=36)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--no-harvest", action="store_true")
    ap.add_argument("--crime", action="store_true", help="only crimes and detentions")
    ap.add_argument("--fill-days", type=float, default=7, help="how far back «добор» looks for uncovered incidents")
    ap.add_argument("--fill-top", type=int, default=10)
    a = ap.parse_args()
    srv = CITIES[a.city].get("server_base")  # on the server its base, on the Mac the owner's folder
    base = Path(a.base or (srv if srv and Path(srv).is_dir() else CITIES[a.city]["base"])).expanduser()
    if not a.no_harvest:
        harvester = CITIES[a.city].get("harvester", "harvest.py")
        cmd = ([sys.executable, "-I", str(HERE / harvester), str(int(a.hours)), "--out", str(base)]
               if harvester != "harvest.py" else
               [sys.executable, "-I", str(HERE / "harvest.py"), str(a.posts), "--city", a.city, "--out", str(base)])
        p = subprocess.run(cmd, capture_output=True, text=True)
        print("\n".join(l for l in p.stdout.splitlines() if re.match(r"  (?:!|\S+: постов|событий:)|   ⇄", l)), flush=True)  # per-channel counts, merges
        if p.returncode != 0:
            sys.exit(f"harvest failed: {p.stderr.strip()[-400:]}")
    events = json.loads((base / "base.json").read_text(encoding="utf-8"))
    links, keys = made_already(REPO)
    now = datetime.now(timezone.utc)
    ranked = sorted((judge(e, now, a.hours, links, keys, a.city, a.crime) for e in events), key=lambda c: -c["score"])
    ok = [c for c in ranked if not c["skip"]]
    fresh = {c["key"] for c in ok}
    fill = [c for c in sorted((judge(e, now, a.fill_days * 24, links, keys, a.city, a.crime) for e in events),
                              key=lambda c: -c["score"])
            if not c["skip"] and c["key"] not in fresh and {"crime", "detention", "accident"} & set(c["tags"])]
    out = {"generated_at": now.isoformat(timespec="seconds"), "base": str(base),
           "candidates": ok, "fill": fill, "skipped": [c for c in ranked if c["skip"]]}
    (base / "candidates.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nкандидаты: {len(ok)} из {len(ranked)} → {base / 'candidates.json'}")
    for i, c in enumerate(ok[:a.top], 1):
        media = describe(c)
        mark = "🔒 " if "detention" in c["tags"] else "🚔 " if "crime" in c["tags"] else ""
        print(f"{i}. {mark}[{c['score']:5.2f}] {c['age_h']:4.1f} ч · {media} · реакций {c['reactions']} · "
              f"{'+'.join(c['channels'])}{' · ⚠ ' + c['note'] if c.get('note') else ''}\n   {c['text'][:150]}")
    if fill:
        print(f"\nдобор — ещё не освещённые ЧП, аресты и задержания за {a.fill_days:g} дн. (брать, если свежих мало):")
    for i, c in enumerate(fill[:a.fill_top], 1):
        media = describe(c)
        mark = "🔒 " if "detention" in c["tags"] else "🚔 " if "crime" in c["tags"] else ""
        print(f"д{i}. {mark}[{c['score']:5.2f}] {c['dt'][:10]} · {media} · реакций {c['reactions']} · "
              f"{'+'.join(c['channels'])}\n   {c['text'][:150]}")
    for c in out["skipped"]:
        print(f"   — пропуск ({', '.join(c['skip'])}): {c['text'][:80]}")


if __name__ == "__main__":
    main()
