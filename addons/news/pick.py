"""Stage 1 of the news workflow: find the city's fresh incidents and crimes and rank them for a «ЧП» short.

    python3 -I addons/news/pick.py [--city moscow|kazan] [--base DIR] [--posts 150] [--hours 36] [--top 5] [--no-harvest] [--crime]

1. Runs harvest.py (Telegram previews -> <base>/base.json + media/), unless --no-harvest.
2. Drops what the format never covers (war / drones / politics, suicide, sexual violence, child victims), events that
   are not in Moscow, older than --hours, and events already made (source links/keys in projects/news-*/story.json).
3. Scores the rest: audience reactions under the post, footage (video > photo > none), how many channels carried
   it, freshness, and how much of a real incident it is (fire / knife / syringe / crash … vs. a curiosity).
   Crimes and above all detentions get extra weight — the format's focus since 2026-10-27; --crime keeps only them.
4. Writes <base>/candidates.json (ranked, with the reasons) and prints the top list.

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
import topics  # noqa: E402

# never covered (README «Какое событие брать»)
SKIP = {
    "война/БПЛА/политика": r"\bВСУ\b|\bСВО\b|БПЛА|беспилот|дрон|ракет|воздушн\w* тревог|в укрыти|обстрел|атак\w* (?:на|ВСУ)|мобилиз|санкци|выбор|депутат|госдум|кремл(?!ёвск|евск)|митинг|теракт|политолог|оппозиц|иноагент|госизмен|экстремист",
    "суицид": r"суицид|самоубий|покончил\w* с собой|свёл счёты|свел счеты",
    "сексуальное насилие": r"изнасил|домогат|педофил|растлен|развращ",
    "дети-жертвы": r"(?:ребён|ребен|младен|малыш|школьни|подрост|\d+-летн\w+ (?:мальчик|девочк))\w*.{0,60}(?:погиб|умер|скончал|пострадал|ранен|сбит|выпал|утонул)",
}
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
    why_not = [name for name, rx in SKIP.items() if re.search(rx, text, re.I)]
    if crime_only and "crime" not in tags:
        why_not.append("не криминал")
    chans = set(e.get("channels") or [])
    c = CITIES[city]
    if not (chans & {h for h, local in c["channels"] if local}) and not re.search(c["geo"], text, re.I):
        why_not.append(f'не {c["name"]}')
    age = age_hours(e.get("dt", ""), now)
    if age > max_hours:
        why_not.append(f"старое ({age:.0f} ч)")
    if e.get("key") in done_keys or set(e.get("links") or []) & done_links:
        why_not.append("уже сделано")
    got = e.get("media_got") or []
    media = 3.0 if "mp4" in got else (1.5 if "photo" in got else 0.0)
    strong = len(set(m.lower() for m in re.findall(STRONG, text, re.I)))
    react = reactions(text)
    crime = "crime" in tags
    score = (math.log10(1 + react) * 1.5 + media + 1.0 * (len(chans) - 1) + min(strong, 3) * 0.8
             + CRIME_BONUS * crime + DETENTION_BONUS * ("detention" in tags)
             - age / 12 - (2.0 if strong == 0 and not crime else 0.0))
    return {"key": e.get("key"), "dt": e.get("dt"), "age_h": round(age, 1), "score": round(score, 2),
            "skip": why_not, "tags": tags, "reactions": react, "media": got, "channels": sorted(chans), "links": e.get("links"),
            "strong_words": strong, "text": clean(text),
            "media_local": [m.get("local") for m in e.get("media", []) if m.get("local")]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="moscow", choices=sorted(CITIES))
    ap.add_argument("--base", default=os.environ.get("NEWS_BASE"), help="default: the city's base in cities.json")
    ap.add_argument("--posts", type=int, default=150)
    ap.add_argument("--hours", type=float, default=36)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--no-harvest", action="store_true")
    ap.add_argument("--crime", action="store_true", help="only crimes and detentions")
    a = ap.parse_args()
    base = Path(a.base or CITIES[a.city]["base"]).expanduser()
    if not a.no_harvest:
        p = subprocess.run([sys.executable, "-I", str(HERE / "harvest.py"), str(a.posts), "--city", a.city,
                            "--out", str(base)],
                           capture_output=True, text=True)
        print("\n".join(l for l in p.stdout.splitlines() if l.startswith("  ")), flush=True)  # per-channel counts
        if p.returncode != 0:
            sys.exit(f"harvest failed: {p.stderr.strip()[-400:]}")
    events = json.loads((base / "base.json").read_text(encoding="utf-8"))
    links, keys = made_already(REPO)
    now = datetime.now(timezone.utc)
    ranked = sorted((judge(e, now, a.hours, links, keys, a.city, a.crime) for e in events), key=lambda c: -c["score"])
    ok = [c for c in ranked if not c["skip"]]
    out = {"generated_at": now.isoformat(timespec="seconds"), "base": str(base),
           "candidates": ok, "skipped": [c for c in ranked if c["skip"]]}
    (base / "candidates.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nкандидаты: {len(ok)} из {len(ranked)} → {base / 'candidates.json'}")
    for i, c in enumerate(ok[:a.top], 1):
        media = "видео" if "mp4" in c["media"] else ("фото" if "photo" in c["media"] else "без медиа")
        mark = "🔒 " if "detention" in c["tags"] else "🚔 " if "crime" in c["tags"] else ""
        print(f"{i}. {mark}[{c['score']:5.2f}] {c['age_h']:4.1f} ч · {media} · реакций {c['reactions']} · "
              f"{'+'.join(c['channels'])}\n   {c['text'][:150]}")
    for c in out["skipped"]:
        print(f"   — пропуск ({', '.join(c['skip'])}): {c['text'][:80]}")


if __name__ == "__main__":
    main()
