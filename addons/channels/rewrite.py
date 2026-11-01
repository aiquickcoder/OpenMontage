"""A story becomes a post in a channel's format: the AI writes it from the sources (first-hand first), the code lays
it out (emoji, bold headline, short paragraphs, the subscribe link to this very channel), the format rules are checked
by code (`problems`, tested) and the facts by a second model (style.CHECK). A post that still fails after one retry
with the reasons is dropped, not published.

Telegram HTML: <b>, <i>, <a href>; everything else is escaped.
"""
from __future__ import annotations

import html
import re
from typing import Any

from addons.channels import config, filters, llm, style

MEDIA_NAMES = re.compile(  # unambiguous outlet and channel names, any case
    r"(?<![а-яёa-z])(?:mash|baza|readovka|ридовк|topor|toporlive|life\.ru|РИА(?: Новости)?|ТАСС|Интерфакс|РБК|"
    r"Коммерсант\w*|Известия|Ведомости|Лента\.ру|Газета\.ru|Москвач\w*|moscowach|Осторожно,? (?:новости|Москва)|"
    r"Раньше всех|Ньюсач|Двач|Varlamov|Варламов\w*|КазаньДА|Казань на максималках|Татар-информ\w*|ТАТМЕДИА|"
    r"БИЗНЕС Online|Бизнес Онлайн|Реальное время|Вечерняя Казань|Москва 24|Мосфид|Mash Iptash|Go Kazan)(?![а-яёa-z])",
    re.I)
MEDIA_CASED = re.compile(  # names that are also words (топор = axe): only as written by the outlets themselves
    r"(?<![А-Яа-яЁёA-Za-z])(?:Топор(?: Live|\+| пишет| сообща)|ТОПОР|SHOT|Shot|Baza|База(?: пишет| сообща)|"
    r"Mash(?![a-z])|Лайф|Life(?![a-z])|Readovka)(?![а-яёa-z])")
SHOCK = re.compile(r"(?<![а-яё])(?:шок|шокир|сенсац|жесть|жуть|офигеть|капец)", re.I)
GUILT = re.compile(r"(?<![а-яё])(?:убийц|насильник|маньяк|педофил|грабител|террорист|вор(?:а|у|ом|ы)?(?![а-яё])|"
                   r"мошенник(?:а|у|ом)?(?![а-яё]))", re.I)
VERDICT = re.compile(r"приговор|осужд|признал\w* виновн|признан\w* виновн|отбыва|срок\w* заключен", re.I)
GENERIC_SCAMMERS = re.compile(r"(?:телефонн|кибер|интернет-)?мошенник(?:и|ов|ам|ами)(?![а-яё])", re.I)
PHONE = re.compile(r"(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}")
PLATE = re.compile(r"(?<![А-ЯA-Z])[АВЕКМНОРСТУХ]\s?\d{3}\s?[АВЕКМНОРСТУХ]{2}\s?\d{2,3}(?![\d])")
FLAT = re.compile(r"(?:кв\.|квартир[аеуы])\s*№?\s*\d+", re.I)
TAG = re.compile(r"<[^>]+>")
FIRST_EMOJI = re.compile("^[\U0001F000-\U0001FAFF☀-➿⬀-⯿‼⁉™ℹ↔-↪⌚-⏿Ⓜ▪-◾〰〽㊗㊙]")
MAX_PLAIN = 1000  # a caption holds 1024


def esc(s: str) -> str:
    return html.escape((s or "").strip(), quote=False)


def cta_html(ch: dict) -> str:
    return f'👉 <a href="https://t.me/{ch["username"]}">{esc(ch.get("cta") or ch.get("title") or "Подписаться")}</a>'


def render(d: dict, ch: dict) -> str:
    emoji = (d.get("emoji") or "⚡️").strip()[:4]
    head = re.sub(r"\s+", " ", d.get("headline") or "").strip()
    head = re.sub(r"^\W+", "", head) if not FIRST_EMOJI.match(head) else head
    who = (d.get("who") or "").strip().strip(".—- ")
    end = ""
    if who:
        end = f", — {esc(who)}."
    elif head and head[-1] not in ".!?…»\"":
        end = "."
    if head.endswith("."):
        head = head[:-1]
        end = end or "."
    parts = [f"{emoji}<b>{esc(head)}</b>{end}"]
    for p in (d.get("paragraphs") or [])[:3]:
        p = re.sub(r"\s+", " ", str(p)).strip()
        if p:
            parts.append(esc(p))
    c = (d.get("comment") or "").strip()
    if c and ch.get("key") != "news":
        parts.append(f"<i>{esc(c)}</i>")
    parts.append(cta_html(ch))
    return "\n\n".join(parts)


def plain(h: str) -> str:
    return html.unescape(TAG.sub("", h or ""))


def problems(h: str, ch: dict) -> list[str]:
    """What breaks the format (empty list = fine)."""
    out = []
    blocks = (h or "").split("\n\n")
    text = plain(h)
    if not FIRST_EMOJI.match(text):
        out.append("пост должен начинаться с эмодзи")
    if not blocks or "<b>" not in blocks[0]:
        out.append("первый абзац (заголовок) должен быть жирным")
    cta = cta_html(ch)
    if not h.rstrip().endswith(cta):
        out.append("в конце должна быть ссылка-подписка на этот канал")
    body = h.replace(cta, "")
    body_plain = plain(body)
    if re.search(r"<a\s", body) or re.search(r"t\.me/|https?://|(?<![\w.])@\w{3,}", body_plain):
        out.append("ссылки и @юзернеймы в тексте запрещены")
    named = MEDIA_NAMES.search(body_plain) or MEDIA_CASED.search(body_plain)
    if named:
        out.append(f"указан источник/СМИ: «{named.group(0)}»")
    paras = [b for b in blocks[1:-1] if not b.startswith("<i>")]
    if len(paras) > 3:
        out.append("больше трёх абзацев")
    if len(text) > MAX_PLAIN:
        out.append(f"слишком длинно: {len(text)} знаков (до {MAX_PLAIN})")
    if SHOCK.search(body_plain):
        out.append(f"запрещённое слово «{SHOCK.search(body_plain).group(0)}»")
    guilt = GUILT.search(GENERIC_SCAMMERS.sub(" ", body_plain))
    if guilt and not VERDICT.search(body_plain):
        out.append(f"обвинение до приговора: «{guilt.group(0)}»")
    if PHONE.search(body_plain) or PLATE.search(body_plain) or FLAT.search(body_plain):
        out.append("личные данные (телефон / номер машины / квартира)")
    if filters.is_war(body_plain):
        out.append("военная тема")
    return out


def sources_text(posts: list[dict], limit: int = 6) -> str:
    """The story's texts for the writer: first-hand first, then by trust and reach."""
    order = sorted(posts, key=lambda p: (p.get("kind") != "primary", -(p.get("trust") or 0.6), -p.get("views", 0)))
    out, seen = [], set()
    for p in order:
        t = (p.get("plain") or "").strip()
        key = t[:120]
        if not t or key in seen:
            continue
        seen.add(key)
        kind = {"primary": "ПЕРВОИСТОЧНИК (ведомство)", "media": "агентство/СМИ", "city": "городской канал",
                "aggregator": "новостной канал"}.get(p.get("kind") or "", "канал")
        out.append(f"[{kind}, {p.get('date_msk', '')}] {t[:1500]}")
        if len(out) >= limit:
            break
    return "\n\n".join(f"ИСТОЧНИК {i + 1}: {s}" for i, s in enumerate(out))


def system_for(db, ch: dict) -> str:
    common = config.setting(db, "style_common") or style.COMMON
    if ch["key"] == "news":
        spec = config.setting(db, "style_news") or style.NEWS
    else:
        spec = (config.setting(db, "style_city") or style.CITY).replace("{{", "{").replace("}}", "}")
        spec = spec.replace("{title}", ch.get("title") or "").replace("{city_gen}", style.CITY_GEN.get(ch["key"], ""))
    extra = (ch.get("style") or "").strip()
    return common + "\n\n" + spec + (("\n\nДОПОЛНИТЕЛЬНО ДЛЯ ЭТОГО КАНАЛА: " + extra) if extra else "")


def write(db, ch: dict, posts: list[dict], title: str = "") -> dict[str, Any]:
    """{"ok", "html", "data", "why", "check"} for one channel."""
    src = sources_text(posts)
    system = system_for(db, ch)
    model = config.setting(db, "model_write")
    checker = config.setting(db, "model_check")
    feedback = ""
    last: dict[str, Any] = {"ok": False, "why": "не написано"}
    for attempt in range(2):
        user = f"Событие: {title}\n\n{src}\n\nНапиши пост." + (f"\n\nПРОШЛАЯ ВЕРСИЯ НЕ ПРОШЛА ПРОВЕРКУ: {feedback}. "
                                                                 "Исправь, не добавляя новых фактов." if feedback else "")
        d = llm.chat_json(db, "write", model, system, user, max_tokens=900, temperature=0.4)
        if not isinstance(d, dict):
            feedback = "ответ не JSON-объект"
            continue
        h = render(d, ch)
        bad = problems(h, ch)
        if bad:
            feedback = "; ".join(bad)
            last = {"ok": False, "html": h, "data": d, "why": feedback}
            continue
        body = plain(h.replace(cta_html(ch), "")).strip()  # the subscribe line is ours, not a fact to check
        verdict = llm.chat_json(db, "check", checker, style.CHECK,
                                f"ИСТОЧНИКИ:\n{src}\n\nПОСТ:\n{body}", max_tokens=600, temperature=0)
        if isinstance(verdict, dict) and verdict.get("ok") and not verdict.get("unsupported") and not verdict.get("problems"):
            return {"ok": True, "html": h, "data": d, "check": verdict}
        issues = (verdict.get("unsupported") or []) + (verdict.get("problems") or []) if isinstance(verdict, dict) else []
        feedback = "; ".join(map(str, issues)) or "проверка не пройдена"
        last = {"ok": False, "html": h, "data": d, "why": "проверка фактов: " + feedback, "check": verdict}
    return last
