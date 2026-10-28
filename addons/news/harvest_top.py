"""Харвестер ЕЖЕДНЕВНОЙ СВОДКИ топ-хайпа РФ (формат «кот зачитывает топ дня»).

Скоуп ГИБРИД (решение юзера 28.10.2026): бытовой / ЧП / weird хайп + крупные
НЕ-военные федеральные инфоповоды (отставки, цены, законы, звёзды).
ВОН: война/СВО/фронт/БПЛА, партийно-электоральная политика, геополитика топ-лидеров,
дискредитация/иноагент (персона кота + юр-риски РФ). Реклама — тоже вон.

Ранжир по ВИРАЛЬНОСТИ: top_views × бонус за кросс-канальность (сколько топ-каналов
пишут ОДИН сюжет — главный сигнал «новость дня»). Веб-превью t.me/s/<ch>.

HTML каналов = НЕДОВЕРЕННЫЕ данные: только регекс-извлечение, ничего не исполняем.

Запуск:  python3 -I harvest_top.py [окно_часов=36] [--out DIR]
Вывод:   DIR/digest.json (= DIR/base.json для pick.py --city russia)  +  media/<key>/{NN_video.mp4, NN_photo.jpg}
         DIR по умолчанию: $NEWS_BASE или ~/memes/rf_top_base (сервер: /srv/factory/news_base_ru).
Владелец собрал его 2026-10-28 для формата «Пушистое ЧП · Россия»; в конвейер перенесён с --out и base.json.
"""
import os, re, sys, json, html, hashlib, urllib.request
from datetime import datetime, timezone
from collections import Counter

_args = sys.argv[1:]
_out = None
if "--out" in _args:
    _i = _args.index("--out"); _out = _args[_i + 1]; del _args[_i:_i + 2]
BASE = os.path.expanduser(_out or os.environ.get("NEWS_BASE") or "~/memes/rf_top_base")
MEDIA = os.path.join(BASE, "media")
os.makedirs(MEDIA, exist_ok=True)
WINDOW_H = int(float(_args[0])) if _args else 36
DL_TOP = 30  # качаем медиа только у топ-N по хайпу (остальным ни к чему)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# топ-хайп каналы РФ (проверено пробником 28.10.2026: веб-превью + max views).
# tier: viral — ядро хайпа; quality — офиц. СМИ для кросс-подтверждения/атрибуции.
CHANNELS = [
    ("readovkanews", "viral"), ("toporlive", "viral"), ("mash", "viral"),
    ("bbbreaking", "viral"), ("bazabazon", "viral"), ("ostorozhno_novosti", "viral"),
    ("shot_shot", "viral"), ("topor", "viral"),
    ("rbc_news", "quality"), ("tass_agency", "quality"),
]

# ВОН: война + партийно-электоральная политика + геополитика топ-лидеров + юр-риск РФ.
# (законы/цены/отставки НЕ режем — «Госдума/Совфед» намеренно НЕ в стоп-листе.)
WARPOL = re.compile(
    r'\bСВО\b|спецоперац|\bВСУ\b|всушник|линия фронта|\bфронт[ае]|передов|наступлени|'
    r'контрнаступ|обстрел|ракетн\w*\s*(удар|атак)|\bБПЛА\b|беспилотн|\bдрон|\bПВО\b|'
    r'мобилизац|Зеленск|\bПутин|Кремл|выбор[ыао]\b|избирательн|оппозиц|навальн|'
    r'митинг|протестн|санкци|Трамп|Байден|\bНАТО\b|Украин|Донбасс|Харьков|\bКиев\b|'
    r'Болсонару|Бразили|дискредитац|\bиноагент|экстремистск', re.I)

# реклама/служебное
NOISE = re.compile(r'реклам|промокод|erid|подпис\w+ на|разыгрыва|скидк|\bбот\b|'
                   r'telegram to view|розыгрыш|партнёрск|при поддержке|промокоду|'
                   r'конф[ау]\b|альфа-конфу|большая игра|встречайте нов|казино|букмекер|'
                   r'ставк[аи] на|\bкрипт|airdrop|\bмерч\b|эксклюзив\w* для подписчик|реферал', re.I)

VIEWS = re.compile(r'tgme_widget_message_views">([\d.,]+[KMкКМ]?)<')

def to_i(s):
    s = s.replace(',', '.').strip(); mult = 1
    if s and s[-1] in 'KкК': mult = 1000; s = s[:-1]
    elif s and s[-1] in 'MМ': mult = 1_000_000; s = s[:-1]
    try: return int(float(s) * mult)
    except Exception: return 0

def fetch(url, rng=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://t.me/",
                                               **({"Range": "bytes=0-"} if rng else {})})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()

def clean(t):
    t = re.sub(r'<br\s*/?>', ' ', t); t = re.sub(r'<[^>]+>', '', t)
    t = html.unescape(t)
    t = re.sub(r'\s*(Please open Telegram to view this post|VIEW IN TELEGRAM)\s*', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()

def parse(ch, raw):
    posts = []
    for c in re.split(r'<div class="tgme_widget_message_bubble"', raw)[1:]:
        m = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*'
                      r'(?:<div class="tgme_widget_message_(?:footer|reply|poll))', c, re.S) \
            or re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', c, re.S)
        text = clean(m.group(1)) if m else ""
        if len(text) < 15:
            continue
        ph = re.search(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", c)
        vd = re.search(r'<video[^>]+src="([^"]+\.mp4[^"]*)"', c)
        dt = re.search(r'<time datetime="([^"]+)"', c)
        ln = re.search(r'tgme_widget_message_date"[^>]*href="([^"]+)"', c)
        vw = VIEWS.search(c)
        posts.append({"ch": ch, "text": text, "photo": ph.group(1) if ph else "",
                      "mp4": vd.group(1) if vd else "",
                      "dt": dt.group(1) if dt else "", "link": ln.group(1) if ln else "",
                      "views": to_i(vw.group(1)) if vw else 0})
    return posts

STOP = set(("который которая которое которые после перед сообщил сообщили сообщает рассказал "
            "рассказала заявил заявила человек человека людей город города россии россия сегодня "
            "вчера также более менее около примерно однако этого этом может могут будет будут были "
            "своих одной одного через из-за сейчас время теперь когда стало стала почти "
            "рублей миллион тысяч").split())
def toks(text):  # содержательные слова длиной 5+ (для кластеризации сюжетов)
    return [w for w in re.findall(r'[а-яёa-z0-9]{5,}', text.lower()) if w not in STOP]

def recent(dtiso):
    if not dtiso: return True  # нет даты — не выкидываем
    try:
        d = datetime.fromisoformat(dtiso.replace("Z", "+00:00"))
        if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).total_seconds() <= WINDOW_H * 3600
    except Exception:
        return True

def tags_for(t):
    tg = []
    if re.search(r'задержа|аресто|уголовн|убий|убил|застрел|зарез|ограб|краж|украл|мошенн|'
                 r'изнасил|наркот|поджог|разбой|стрельб|напал|избил|под стражу|пригов|осуд', t, re.I): tg.append("crime")
    if re.search(r'пожар|гор(ит|ел|ящ)|взрыв|задым|поджог', t, re.I): tg.append("fire")
    if re.search(r'ДТП|авар|столкн|наезд|сбил|перевернул', t, re.I): tg.append("dtp")
    if re.search(r'погиб|смерт|насмерть|скончал|\bтруп', t, re.I): tg.append("fatal")
    if re.search(r'блогер|актёр|актрис|певиц|певец|рэпер|звезд|инфлюенсер|шоумен|артист|'
                 r'телеведущ|комик|музыкант|продюсер', t, re.I): tg.append("celeb")
    if re.search(r'цен[аы]|подорожа|подешеве|тариф|\bрубл|инфляц|зарплат|пенси|налог|выплат', t, re.I): tg.append("money")
    if re.search(r'закон|законопроект|запрет|штраф|пошлин|\bМРОТ\b|разреш\w+ с \d', t, re.I): tg.append("law")
    if re.search(r'упал|выпал|провал|обруш|потоп|затоп|наводн|ураган|смерч|\bград\b|ливень|'
                 r'землетряс|оползен|лавин', t, re.I): tg.append("disaster")
    return tg or ["weird"]

# --- собрать и отфильтровать ---
all_posts, dropped = [], []
tier = dict(CHANNELS)
def channel_posts(ch, pages=8):
    """t.me/s shows ~20 posts a page: walk back (?before=<id>) until the window is covered — busy channels (Topor)
    post 20 times in a few hours, and the morning run needs yesterday's daytime too."""
    out, before = [], ""
    for _ in range(pages):
        raw = fetch(f"https://t.me/s/{ch}{before}").decode("utf-8", "replace")
        got = parse(ch, raw)
        out += got
        ids = [int(m) for m in re.findall(r'data-post="[^/"]+/(\d+)"', raw)]
        if not got or not ids or all(not recent(p["dt"]) for p in got[:3]):
            break
        before = f"?before={min(ids)}"
    return out

for ch, _ in CHANNELS:
    try:
        posts_ch = channel_posts(ch)
    except Exception as e:
        print(f"  ! {ch}: {e}"); continue
    kept = 0
    for p in posts_ch:
        if NOISE.search(p["text"]): continue
        if not recent(p["dt"]): continue
        if WARPOL.search(p["text"]):
            dropped.append((ch, p["text"])); continue
        all_posts.append(p); kept += 1
    print(f"  {ch}: в окне {WINDOW_H}ч отобрано {kept}")

# --- кластеризация «один сюжет» по общим РЕДКИМ словам (кросс-канальный мерж) ---
df = Counter()
for p in all_posts:
    for w in set(toks(p["text"])): df[w] += 1
NP = max(1, len(all_posts))
def rare_set(text):  # все специфичные слова поста (редкие сущности, не общие крим-глаголы)
    return {w for w in set(toks(text)) if df[w] <= max(2, NP * 0.18)}

posts = sorted(all_posts, key=lambda x: x["dt"])
rs = [rare_set(p["text"]) for p in posts]
parent = list(range(len(posts)))
def find(a):
    while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
    return a
for i in range(len(posts)):
    for j in range(i + 1, len(posts)):
        if len(rs[i] & rs[j]) >= 3:  # >=3 общих редких слова => один и тот же сюжет
            ra, rb = find(i), find(j)
            if ra != rb: parent[rb] = ra

events = {}
for idx, p in enumerate(posts):
    r = find(idx)
    e = events.get(r)
    if not e:
        events[r] = e = {"dt": p["dt"][:16], "text": p["text"],
                         "channels": [], "views": {}, "links": [], "media": []}
    if p["ch"] not in e["channels"]:
        e["channels"].append(p["ch"]); e["links"].append(p["link"])
    e["views"][p["ch"]] = max(e["views"].get(p["ch"], 0), p["views"])
    if len(p["text"]) > len(e["text"]): e["text"] = p["text"]
    if p["dt"][:16] < e["dt"]: e["dt"] = p["dt"][:16]  # самый ранний = момент сюжета
    for kind, url in (("mp4", p["mp4"]), ("photo", p["photo"])):
        if url and not any(m["url"] == url for m in e["media"]):
            e["media"].append({"type": kind, "url": url, "ch": p["ch"], "local": ""})

# --- хайп-скор: top_views × бонус за кросс-канальность ---
for e in events.values():
    e["top_views"] = max(e["views"].values()) if e["views"] else 0
    e["n_channels"] = len(e["channels"])
    e["viral"] = any(tier.get(c) == "viral" for c in e["channels"])
    e["hype"] = int(e["top_views"] * (1 + 0.4 * (e["n_channels"] - 1)))
    e["tags"] = tags_for(e["text"])
    e["key"] = hashlib.md5(" ".join(re.sub(r'[^а-яёa-z0-9 ]', '', e["text"].lower())
                                    .split()[:7]).encode()).hexdigest()[:10]

out = sorted(events.values(), key=lambda x: (-x["hype"], x["dt"]))

# --- качаем медиа только у топ-DL_TOP по хайпу ---
def dl(url, dst, rng):
    try:
        data = fetch(url, rng=rng)
        if len(data) < 1024: return False
        open(dst, "wb").write(data); return True
    except Exception:
        return False

for e in out[:DL_TOP]:
    d = os.path.join(MEDIA, e["key"]); got = []
    for i, m in enumerate(e["media"]):
        ext = "mp4" if m["type"] == "mp4" else "jpg"
        dst = os.path.join(d, f'{i:02d}_{m["type"]}.{ext}')
        os.makedirs(d, exist_ok=True)
        if dl(m["url"], dst, rng=(m["type"] == "mp4")):
            m["local"] = dst; got.append(m["type"])
    e["media_got"] = got

for e in out:  # pick.py reads media[].local and media_got like the city bases
    e.setdefault("media_got", [])
json.dump(out, open(os.path.join(BASE, "digest.json"), "w"), ensure_ascii=False, indent=2)
json.dump(out, open(os.path.join(BASE, "base.json"), "w"), ensure_ascii=False, indent=2)

def kview(n): return f"{n/1000:.0f}K" if n < 1_000_000 else f"{n/1_000_000:.1f}M"
print(f"\n=== СВОДКА РФ · окно {WINDOW_H}ч · {len(out)} сюжетов · отсеяно war/polit {len(dropped)} "
      f"→ digest.json (топ-{DL_TOP} с медиа) ===")
for i, e in enumerate(out[:20], 1):
    media = "".join({"mp4": "🎬", "photo": "🖼"}.get(g, "") for g in e.get("media_got", [])) or "·"
    chs = "+".join(e["channels"])
    tg = ",".join(e["tags"])
    print(f'{i:2}. {kview(e["top_views"]):>5} ×{e["n_channels"]} [{media:3}] {tg:<14} {chs:<28} {e["text"][:60]}')
if dropped:
    print(f"\n--- примеры отсеянного war/polit ({len(dropped)}): ---")
    for ch, t in dropped[:8]:
        print(f"   [{ch}] {t[:70]}")
