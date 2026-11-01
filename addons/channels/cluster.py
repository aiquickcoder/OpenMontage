"""One event, many posts: a new post joins the story whose posts share its rare words (pure logic, tested).

Words are cut to their first 6 letters (Russian endings: «пожаре», «пожара» → «пожар»), common words are dropped,
and each shared word weighs by its rarity in the window (IDF over the posts read in the last story_window_h hours):
two posts about one fire on Баумана share «баума», «двухэ», «40» — rare — while a city's feed shares «водит» and
«автом» everywhere. Numbers (ages, counts, sums) and names (capitalised mid-sentence) count extra.

The AI judge then merges by meaning what words could not (judge.py: «same_as»), and checks every story against what
the channels already published — the second and third layer of the dedupe (as in influencer-network's news engine:
title signature → Jaccard → LLM «ДУБЛЬ/НОВОЕ»).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable

STOP = set("""
котор после перед через более менее около между также может могут будет будут были было стало стала стали сейчас
теперь когда сегод вчера завтр утром вечер ночью время годов годам несколь сообщ заявил заявлен рассказ пишут пишет
отмети уточни добави сказал говори человек людей мужчин женщин россия россии россий москва москве москвы москов
казань казани питер петерб регион област город города районе района улице улица своей своих этого этому этой
очень всего всех только просто прямо почти тысяч миллио рублей долла процен случае причин видео фото кадры кадр
подпис канал новост telegr телегр источн данным данные отмеча которы которые которая которое никого ничего
пострад задерж полиц подроб момент появил оказал произош случил решил получил начал стоит может нужно можно
""".split())
WORD = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё-]{4,}")
NUM = re.compile(r"(?<![\d.,])(\d{2,})(?![\d])")
SENT = re.compile(r"(?<=[.!?…])\s+|\n+")
YEAR = re.compile(r"^(?:19|20)\d\d$")


def norm(w: str) -> str:
    return w.lower().replace("ё", "е")[:6]


def stems(text: str) -> set[str]:
    return {s for s in (norm(w) for w in WORD.findall(text or "")) if s[:5] not in STOP and s not in STOP}


def numbers(text: str) -> set[str]:
    return {n for n in NUM.findall(text or "") if not YEAR.match(n)}


def names(text: str) -> set[str]:
    """Capitalised words that do not open a sentence: places, people, organisations."""
    out = set()
    for sent in SENT.split(text or ""):
        for w in WORD.findall(sent)[1:]:
            if w[0].isupper() and norm(w)[:5] not in STOP:
                out.add(norm(w))
    return out


def signature(text: str) -> dict:
    return {"s": sorted(stems(text)), "n": sorted(numbers(text)), "c": sorted(names(text))}


class Index:
    """IDF over the window's posts."""

    def __init__(self, sigs: Iterable[dict] = ()):
        self.df: Counter = Counter()
        self.n = 0
        for s in sigs:
            self.add(s)

    def add(self, sig: dict) -> None:
        self.n += 1
        self.df.update(set(sig["s"]))

    def idf(self, w: str) -> float:
        return math.log((self.n + 1) / (self.df.get(w, 0) + 1)) + 0.1

    def rare(self, w: str, share: float = 0.03) -> bool:
        return self.df.get(w, 0) <= max(2, self.n * share)


DIGEST_LINE = re.compile(r"^\s*(?:[▪•●◾️▫️◽️➖—–-]|\d[.)]|\d\ufe0f?\u20e3|[\U0001F300-\U0001FAFF])\s*\S", re.M)


def is_digest(text: str) -> bool:
    """A round-up of several stories (bullets, numbered or emoji-led lines): it would glue unrelated stories together
    (aifonline's «▪ Иран… ▪ умер бэк-вокалист «Любэ»… ▪ Шнайдер…» merged three stories, 2026-11-01)."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return sum(1 for ln in lines if DIGEST_LINE.match(ln)) >= 3 and len(lines) >= 4


def similarity(a: dict, b: dict, idx: Index) -> tuple[float, int]:
    """(score 0..~1.6, shared rare words). Score = IDF-weighted overlap of the smaller post + numbers + names, but
    never more than twice the cosine: a long post shares something with everything."""
    sa, sb = set(a["s"]), set(b["s"])
    if not sa or not sb:
        return 0.0, 0
    shared = sa & sb
    wa = sum(idx.idf(w) for w in sa)
    wb = sum(idx.idf(w) for w in sb)
    common = sum(idx.idf(w) for w in shared)
    score = min(common / max(1e-6, min(wa, wb)), 2 * common / max(1e-6, math.sqrt(wa * wb)))
    nums = set(a["n"]) & set(b["n"])
    score += min(0.3, 0.15 * len(nums))
    caps = set(a["c"]) & set(b["c"])
    score += min(0.3, 0.1 * len(caps))
    rare = sum(1 for w in shared if idx.rare(w)) + len(nums)
    return score, rare


def same_event(a: dict, b: dict, idx: Index) -> bool:
    score, rare = similarity(a, b, idx)
    return (score >= 0.42 and rare >= 3) or (score >= 0.6 and rare >= 2)


def best_story(sig: dict, candidates: list[tuple[int, dict]], idx: Index, seeds: dict[int, dict] | None = None,
               seed_min: float = 0.3) -> int | None:
    """The story id (of (story_id, post_sig) pairs) the post belongs to, by its closest post — and only if it also
    resembles the story's first post (seeds): without that anchor a chain of half-related posts glues different
    events together (Путин—Зеленский and Новак's diesel, 2026-11-01)."""
    best: tuple[float, int | None] = (0.0, None)
    for story_id, other in candidates:
        if same_event(sig, other, idx):
            score, _ = similarity(sig, other, idx)
            if score > best[0]:
                best = (score, story_id)
    sid = best[1]
    if sid is not None and seeds and sid in seeds:
        score, rare = similarity(sig, seeds[sid], idx)
        if score < seed_min or rare < 1:
            return None
    return sid
