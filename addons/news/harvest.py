"""Харвестер базы ЧП города (Москва, Казань, Петербург — cities.json) из Telegram-каналов (веб-превью t.me/s/<ch>).
Тянет набор каналов → фильтрует происшествия и преступления города → дедуп по тексту →
качает медиа (фото/видео) СРАЗУ (токены в ссылках короткоживущие) → base.json + crime.json.
Каждое событие помечено тегами topics.py (crime, detention, accident, fire, dtp, fatal);
crime.json — срез с тегом crime (задержания, убийства, кражи, мошенничество, суд…).

Запуск:  python3 -I addons/news/harvest.py [N_постов_на_канал] [--city moscow|kazan|spb] [--out DIR]
Вывод:   DIR/base.json  +  DIR/crime.json  +  DIR/media/<key>/{NN_mp4.mp4,NN_photo.jpg}
         DIR по умолчанию: $NEWS_BASE или base города из cities.json (сервер: server_base).
Свежая база ПЕРЕЗАПИСЫВАЕТ base.json, но медиа прошлых прогонов остаются в media/ (ключ события стабилен).

HTML каналов = НЕДОВЕРЕННЫЕ данные: только регекс-извлечение, ничего не исполняем.
"""
import os, re, sys, json, html, time, hashlib, subprocess, urllib.request, urllib.error
from pathlib import Path
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import topics  # noqa: E402
import sources  # noqa: E402  (official video channels and news sites, owner 2026-10-30)

_args = sys.argv[1:]
_opt = {}
for _k in ("--out", "--city"):
    if _k in _args:
        _i = _args.index(_k)
        _opt[_k] = _args[_i + 1]
        del _args[_i:_i + 2]
CITY_NAME = _opt.get("--city", "moscow")
CITY = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cities.json"), encoding="utf-8"))[CITY_NAME]
BASE = os.path.expanduser(_opt.get("--out") or os.environ.get("NEWS_BASE") or CITY["base"])
MEDIA = os.path.join(BASE, "media")
os.makedirs(MEDIA, exist_ok=True)
N = int(_args[0]) if _args else 20
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# (handle, городской-ли канал целиком) из cities.json. Для федеральных применяем гео-фильтр города.
CHANNELS = [tuple(c) for c in CITY["channels"]]

GEO = re.compile(CITY["harvest_geo"], re.I)
NOISE = re.compile(r'реклам|промокод|подпис\w+ на|erid|разыгрыва|скидк|\bбот\b|telegram to view|'
                   r'кухонн\w* нож|набор\w* нож|мурал|памятник погибш|в память погибш', re.I)

def fetch(url, rng=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://t.me/",
                                               **({"Range": "bytes=0-"} if rng else {})})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()

def clean(t):
    t = re.sub(r'<br\s*/?>', ' ', t)
    t = re.sub(r'<[^>]+>', '', t)
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
        # every picture of the post, albums included (owner 2026-10-29: real footage instead of AI pictures); a video
        # too big for the web preview has no src, its preview frame then comes as a photo
        vids = re.findall(r'<video[^>]+src="([^"]+\.mp4[^"]*)"', c)
        pics = re.findall(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", c)
        # a video too big for the preview («Media is too big») has no src: tg_client.py fetches it whole through the
        # app's API (2026-10-30); its preview frame stays as a photo in case that fails
        big = re.findall(r'tgme_widget_message_video_player not_supported[^>]*href="https://t\.me/([^"/?]+)/(\d+)', c)
        if not vids or big:
            pics += re.findall(r"tgme_widget_message_video_thumb[^>]*background-image:url\('([^']+)'\)", c)
        vids += list(dict.fromkeys(f"tg:{a}/{b}" for a, b in big))
        dt = re.search(r'<time datetime="([^"]+)"', c)
        ln = re.search(r'tgme_widget_message_date"[^>]*href="([^"]+)"', c)
        posts.append({"ch": ch, "text": text, "photo": pics[0] if pics else "", "photos": pics[:10],
                      "mp4": vids[0] if vids else "", "mp4s": vids[:6],
                      "dt": dt.group(1)[:16] if dt else "", "link": ln.group(1) if ln else ""})
    return posts

def sig(text):
    s = re.sub(r'[^а-яёa-z0-9 ]', '', text.lower())
    return " ".join(s.split()[:7])  # первые 7 слов как подпись события

# --- собрать и отфильтровать ---
all_posts = []
def channel_posts(ch, n):
    """The channel's last ~n posts: t.me/s shows ~20 per page, older pages via ?before=<oldest id>."""
    posts, before = [], None
    for _ in range(max(1, n // 15 + 2)):
        raw = fetch(f"https://t.me/s/{ch}" + (f"?before={before}" if before else "")).decode("utf-8", "replace")
        ids = [int(i) for i in re.findall(rf'data-post="{re.escape(ch)}/(\d+)"', raw)]
        posts += parse(ch, raw)
        if not ids or len(posts) >= n or (before and min(ids) >= before):  # enough, or no older page
            break
        before = min(ids)
        time.sleep(1.0)
    return posts

for ch, is_msk in CHANNELS:
    try:
        posts = channel_posts(ch, N)
    except Exception as e:
        print(f"  ! {ch}: {e}"); continue
    kept = 0
    for p in posts:
        if NOISE.search(p["text"]): continue
        if not topics.is_event(p["text"]): continue
        if not is_msk and not GEO.search(p["text"]): continue
        all_posts.append(p); kept += 1
    span = f'{min(p["dt"] for p in posts)}…{max(p["dt"] for p in posts)}' if posts else "-"
    print(f"  {ch}: постов {len(posts)} ({span}), ЧП-постов отобрано {kept}")

# --- не только Telegram (владелец 2026-10-30): Rutube МВД России, YouTube НАК, сайты (ТНВ, sledcom.ru) — sources.py;
# в cities.json "sources": [вид, адрес, только-этот-город, официальный] ---
for spec in CITY.get("sources", []):
    try:
        got = sources.posts(spec)
    except Exception as e:
        print(f"  ! {spec[0]}:{spec[1]}: {e}"); continue
    kept = 0
    for p in got:
        if NOISE.search(p["text"]) or not topics.is_event(p["text"]): continue
        if not (len(spec) > 2 and spec[2]) and not GEO.search(p["text"]): continue
        all_posts.append(p); kept += 1
    print(f"  {spec[0]}:{spec[1]}: постов {len(got)}, ЧП-постов отобрано {kept}")

# --- один случай из всех каналов: та же подпись или ≥3 общих редких слова в пределах 3 суток (2026-10-29: пост
# ведомства и перепосты местных каналов об одном случае — один сюжет, со всеми их кадрами) ---
STOP = set(("который которая которое которые после перед сообщил сообщили сообщает рассказал рассказала заявил "
             "заявила человек человека людей город города россии россия сегодня вчера также более менее около "
             "примерно однако этого этом может могут будет будут были своих одной одного через сейчас время "
             "теперь когда стало стала почти рублей миллион тысяч казани казань москве москва петербурге "
             "татарстане полиция полиции задержали").split())

def toks(text):
    return [w for w in re.findall(r"[а-яёa-z0-9]{5,}", text.lower()) if w not in STOP]

def hours(a, b):
    try:
        return abs((datetime.fromisoformat(a[:16]) - datetime.fromisoformat(b[:16])).total_seconds()) / 3600
    except ValueError:
        return 0.0

posts = sorted(all_posts, key=lambda x: x["dt"])
df = Counter(w for q in posts for w in set(toks(q["text"])))
# a city's incident feed shares «водитель», «автомобиль», «трасса» everywhere: rare = in at most 3% of the posts
# (the Russia harvester's 18% merged unrelated crashes into one, 2026-10-29)
rare = [{w for w in set(toks(q["text"])) if df[w] <= max(2, len(posts) * 0.03)} for q in posts]
parent = list(range(len(posts)))
def find(a):
    while parent[a] != a:
        parent[a] = parent[parent[a]]; a = parent[a]
    return a
def join(a, b):
    ra, rb = find(a), find(b)
    if ra != rb: parent[max(ra, rb)] = min(ra, rb)  # the earliest post names the event (its key stays the same)
first = {}
for i, q in enumerate(posts):
    k = sig(q["text"])
    if k in first: join(first[k], i)
    else: first[k] = i
for i in range(len(posts)):
    for j in range(i + 1, len(posts)):
        if hours(posts[i]["dt"], posts[j]["dt"]) > 72: break
        both = len(rare[i] & rare[j])
        if both >= 3 and both >= 0.34 * min(len(rare[i]), len(rare[j])): join(i, j)

groups = {}
for i in range(len(posts)):
    groups.setdefault(find(i), []).append(i)
joined = [g for g in groups.values() if len({posts[i]["ch"] for i in g}) > 1]
print(f"  событий: {len(groups)} из {len(posts)} постов; из нескольких каналов: {len(joined)}")
for g in joined[:5]:
    print("   ⇄ " + " | ".join(f'{posts[i]["ch"]}: {posts[i]["text"][:50]}' for i in g[:3]))
events = {}
for i, p in enumerate(posts):
    r = find(i)
    e = events.get(r)
    if not e:
        k = sig(posts[r]["text"])
        events[r] = e = {"key": hashlib.md5(k.encode()).hexdigest()[:10], "dt": posts[r]["dt"],
                         "text": p["text"], "channels": [], "links": [], "media": [], "official": False}
    e["official"] = e["official"] or bool(p.get("official"))  # the agency's own footage: shown as published
    if p["ch"] not in e["channels"]:
        e["channels"].append(p["ch"]); e["links"].append(p["link"])
    if len(p["text"]) > len(e["text"]): e["text"] = p["text"]  # самый полный вариант
    for kind, urls in (("mp4", p.get("mp4s") or [p["mp4"]]), ("photo", p.get("photos") or [p["photo"]])):
        for url in urls:
            if url and not any(m["url"] == url for m in e["media"]) and \
                    sum(m["type"] == kind for m in e["media"]) < (6 if kind == "mp4" else 10):
                e["media"].append({"type": kind, "url": url, "ch": p["ch"], "local": ""})

# --- скачать медиа сразу ---
def dl(url, dst, rng):
    if url.startswith(("rutube:", "youtube:", "tg:")):  # a big video: fetched once
        return (os.path.getsize(dst) > 1024 if os.path.isfile(dst) else False) or sources.fetch_video(url, Path(dst))
    try:
        data = fetch(url, rng=rng)
        if len(data) < 1024: return False
        open(dst, "wb").write(data); return True
    except Exception:
        return False

for e in events.values():
    d = os.path.join(MEDIA, e["key"]);
    got = []
    for i, m in enumerate(e["media"]):
        ext = "mp4" if m["type"] == "mp4" else "jpg"
        dst = os.path.join(d, f'{i:02d}_{m["type"]}.{ext}')
        os.makedirs(d, exist_ok=True)
        if dl(m["url"], dst, rng=(m["type"] == "mp4")):
            m["local"] = dst; got.append(m["type"])
    e["media_got"] = got

out = sorted(events.values(), key=lambda x: x["dt"], reverse=True)
for e in out:
    e["tags"] = topics.tags(e["text"])
json.dump(out, open(os.path.join(BASE, "base.json"), "w"), ensure_ascii=False, indent=2)
crime = [e for e in out if "crime" in e["tags"]]
json.dump(crime, open(os.path.join(BASE, "crime.json"), "w"), ensure_ascii=False, indent=2)

det = sum("detention" in e["tags"] for e in out)
print(f"\n=== БАЗА: {len(out)} событий · {len(crime)} крим · {det} задержаний → base.json + crime.json ===")
for e in out:
    media = "".join({"mp4": "🎬", "photo": "🖼"}.get(g, "") for g in e.get("media_got", [])) or "·"
    mark = "🔒" if "detention" in e["tags"] else "🚔" if "crime" in e["tags"] else "  "
    print(f'{mark}[{e["dt"]}] {media:3} {"+".join(e["channels"]):<22} {e["text"][:88]}')
