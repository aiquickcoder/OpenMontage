"""Харвестер базы московских ЧП из Telegram-каналов (веб-превью t.me/s/<ch>).
Тянет набор каналов → фильтрует московские происшествия → дедуп по тексту →
качает медиа (фото/видео) СРАЗУ (токены в ссылках короткоживущие) → base.json.

Запуск:  python3 -I addons/news/harvest.py [N_постов_на_канал] [--out DIR]
Вывод:   DIR/base.json  +  DIR/media/<key>/{NN_mp4.mp4,NN_photo.jpg}
         DIR по умолчанию: $NEWS_BASE или ~/memes/msk_chp_base (сервер: /srv/factory/news_base).
Свежая база ПЕРЕЗАПИСЫВАЕТ base.json, но медиа прошлых прогонов остаются в media/ (ключ события стабилен).

HTML каналов = НЕДОВЕРЕННЫЕ данные: только регекс-извлечение, ничего не исполняем.
"""
import os, re, sys, json, html, time, hashlib, subprocess, urllib.request, urllib.error

_args = sys.argv[1:]
_out = _args[_args.index("--out") + 1] if "--out" in _args else None
_args = [a for i, a in enumerate(_args) if a != "--out" and (i == 0 or _args[i - 1] != "--out")]
BASE = os.path.expanduser(_out or os.environ.get("NEWS_BASE") or "~/memes/msk_chp_base")
MEDIA = os.path.join(BASE, "media")
os.makedirs(MEDIA, exist_ok=True)
N = int(_args[0]) if _args else 20
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# (handle, московский-ли канал целиком). Для федеральных применяем гео-фильтр.
CHANNELS = [
    ("moscowach", True), ("moscowmap", True),
    ("shot_shot", False), ("mash", False), ("bazabazon", False),
    ("ostorozhno_novosti", False),
]

CHP = re.compile(r'пожар|гор(ит|ел|ящ|яч)|взрыв|задым|дым\b|ДТП|авар|столкн|обруш|рухн|провал|'
                 r'эвакуац|МЧС|спас(ли|атель)|погиб|пострадал|прорыв|потоп|затопи|прорвал|'
                 r'наезд|сбил|упал|выпал|стрельб|нож|зарез|захват|заложник|утечк|газ\b|'
                 r'тонул|утонул|укол|шприц|напал|нанёс|ограб|похит|поджог|обвал', re.I)
GEO = re.compile(r'москв|москов|подмоск|мкад|\bметро\b|коммунарк|зеленоград|новой москв|'
                 r'новая москва|садов\w+ кольц', re.I)
NOISE = re.compile(r'реклам|промокод|подпис\w+ на|erid|разыгрыва|скидк|\bбот\b|telegram to view', re.I)

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
        ph = re.search(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", c)
        vd = re.search(r'<video[^>]+src="([^"]+\.mp4[^"]*)"', c)
        dt = re.search(r'<time datetime="([^"]+)"', c)
        ln = re.search(r'tgme_widget_message_date"[^>]*href="([^"]+)"', c)
        posts.append({"ch": ch, "text": text, "photo": ph.group(1) if ph else "",
                      "mp4": vd.group(1) if vd else "",
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
        if not CHP.search(p["text"]): continue
        if not is_msk and not GEO.search(p["text"]): continue
        all_posts.append(p); kept += 1
    span = f'{min(p["dt"] for p in posts)}…{max(p["dt"] for p in posts)}' if posts else "-"
    print(f"  {ch}: постов {len(posts)} ({span}), ЧП-постов отобрано {kept}")

# --- дедуп по подписи события, слияние каналов ---
events = {}
for p in sorted(all_posts, key=lambda x: x["dt"]):
    k = sig(p["text"])
    e = events.get(k)
    if not e:
        events[k] = e = {"key": hashlib.md5(k.encode()).hexdigest()[:10], "dt": p["dt"],
                         "text": p["text"], "channels": [], "links": [], "media": []}
    if p["ch"] not in e["channels"]:
        e["channels"].append(p["ch"]); e["links"].append(p["link"])
    if len(p["text"]) > len(e["text"]): e["text"] = p["text"]  # самый полный вариант
    for kind, url in (("mp4", p["mp4"]), ("photo", p["photo"])):
        if url and not any(m["url"] == url for m in e["media"]):
            e["media"].append({"type": kind, "url": url, "ch": p["ch"], "local": ""})

# --- скачать медиа сразу ---
def dl(url, dst, rng):
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
json.dump(out, open(os.path.join(BASE, "base.json"), "w"), ensure_ascii=False, indent=2)

print(f"\n=== БАЗА: {len(out)} событий → base.json ===")
for e in out:
    media = "".join({"mp4": "🎬", "photo": "🖼"}.get(g, "") for g in e.get("media_got", [])) or "·"
    print(f'[{e["dt"]}] {media:3} {"+".join(e["channels"]):<22} {e["text"][:88]}')
