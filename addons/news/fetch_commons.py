"""Скачать конкретные файлы Wikimedia Commons (CC) + сохранить атрибуцию.
Usage: python3 -I fetch_commons.py <outdir> <title1> [title2 ...]
Также режим поиска: python3 -I fetch_commons.py <outdir> --search "query" <n>
"""
import sys, os, re, json, urllib.request, urllib.parse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import library  # noqa: E402  (every found photo goes to the base of real pictures, owner 2026-10-30)

API = "https://commons.wikimedia.org/w/api.php"
UA = "cat-news-research/1.0 (illustration sourcing; contact: local)"
outdir = sys.argv[1]; os.makedirs(outdir, exist_ok=True)

def get(params):
    params = {**params, "format": "json"}
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return json.load(urllib.request.urlopen(req, timeout=30))

def strip(s): return re.sub(r'<[^>]+>', '', s or '').strip()

titles = []
if len(sys.argv) > 2 and sys.argv[2] == "--search":
    q = sys.argv[3]; n = int(sys.argv[4]) if len(sys.argv) > 4 else 5
    d = get({"action": "query", "generator": "search", "gsrsearch": q,
             "gsrnamespace": 6, "gsrlimit": n, "prop": "imageinfo", "iiprop": "url"})
    titles = [p["title"] for p in (d.get("query", {}).get("pages", {}) or {}).values()]
    print("found:", *titles, sep="\n  ")
else:
    titles = ["File:" + t if not t.startswith("File:") else t for t in sys.argv[2:]]

creds = open(os.path.join(outdir, "CREDITS.txt"), "a", encoding="utf-8")
for t in titles:
    d = get({"action": "query", "titles": t, "prop": "imageinfo",
             "iiprop": "url|extmetadata|size", "iiurlwidth": 1800})
    for p in (d.get("query", {}).get("pages", {}) or {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        em = ii.get("extmetadata") or {}
        lic = strip((em.get("LicenseShortName") or {}).get("value", "?"))
        art = strip((em.get("Artist") or {}).get("value", "?"))
        src = ii.get("thumburl") or ii.get("url")
        if not src: print("  ! no url:", t); continue
        stem, ext = os.path.splitext(t.replace("File:", ""))
        fn = re.sub(r'[^\wА-Яа-яЁё.-]', '_', stem)[:60] + ext.lower()  # keep Cyrillic and the extension
        dst = os.path.join(outdir, fn)
        try:
            req = urllib.request.Request(src, headers={"User-Agent": UA})
            open(dst, "wb").write(urllib.request.urlopen(req, timeout=60).read())
            print(f"  ✓ {fn}  [{lic}]  {art[:40]}")
            page = f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(t.replace(' ','_'))}"
            creds.write(f"{fn}\n  {t}\n  Author: {art}\n  License: {lic}\n  Source: {page}\n\n")
            try:
                tags = (q.split() if titles and len(sys.argv) > 2 and sys.argv[2] == "--search" else []) + ["commons"]
                library.add(Path(dst), tags + re.findall(r"[А-Яа-яЁёA-Za-z]{4,}", stem)[:6],
                            credit=f"Фото: {art} / Wikimedia, {lic}", license=lic, source=page)
            except OSError as e:
                print("  ! library:", e)
        except Exception as e:
            print("  ! fail", t, e)
creds.close()
