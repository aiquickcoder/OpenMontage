"""A quote as a component, not a zoomed screenshot (owner 2026-11-03: «такие кадры с текстом просто нужно упаковывать
в UI типа как твит / или новость, красиво сделанный и сильно не приближать, а то текст не видно… в компоненте
отображаешь оригинал, а далее там же переведённый текст нативно, не субтитрами. И для таких кадров нужен небольшой
наезд камеры»).

A shot in story.json:
    {"src": "media/cards/vance.png", "role": "quote", "beat": "…",
     "quote_card": {"kind": "post", "name": "JD Vance", "handle": "@JDVance", "platform": "X",
                    "avatar": "media/cards/vance_face.jpg", "verified": true,
                    "text": "<the original words, verbatim>", "date": "2 ноября", "url": "https://…"},
     "translation": "<the Russian, shown inside the card under the original; none for a Russian quote>"}
    kind "news": {"kind": "news", "outlet": "The Wall Street Journal", "headline": "<original headline>",
                  "text": "<optional: the article's line>", "date": …, "url": …}
"src" (the screenshot of the original — our proof) runs blurred behind the card; build.py draws the card in the
film's HTML over it: white, whole, inside the text-safe zone, under the hook and above the captions, with a gentle
push-in. No platform logos (a foreign logo reads as a watermark), the platform by name.

    .venv/bin/python addons/news/quote_card.py preview <cards.json> <out.png> [--bg <picture>]   # the card alone
    .venv/bin/python addons/news/quote_card.py avatars [key,key] [--force]   # the popular sources' pictures
"""
from __future__ import annotations

import html as _html
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

W, H = 1080, 1920
LEFT = RIGHT = 120           # the text-safe zone (build.py SAFE_X): a phone taller than 9:16 loses ~100 px a side
TOP_MIN = 548                # under the hook card (at most three lines of it end ~474 px)
BOTTOM = 1282                # above the captions' room (SAFE_BOTTOM 1500 − CAP_ROOM 180 − 18) with room for the push-in
PAD_TOP, PAD_BOTTOM, PAD_X = 34, 30, 38
INNER_W = W - LEFT - RIGHT - 2 * PAD_X
PUSH = 0.04                  # «небольшой наезд камеры»: 1.00 → 1.04 over the shot
SIZES = (46, 42, 38, 35, 32, 29, 27)
CHAR_EM = 0.58               # Inter's mean advance for mixed Latin/Cyrillic text, with room for word wrap
HERE = Path(__file__).resolve().parent
AVATARS = HERE / "avatars"   # the popular sources' pictures (quote_card.py avatars): <key>.jpg
SOURCES = {k: v for k, v in json.loads((HERE / "quote_sources.json").read_text(encoding="utf-8")).items()
           if not k.startswith("_")}
MAST_H = 100                 # a news card's masthead in the outlet's colours
HEAD_FONT = {"serif": '"Playfair Display", "PT Serif", Georgia, serif',
             "sans": '"Inter", "DejaVu Sans", Arial, sans-serif',
             "condensed": '"Roboto Condensed", "Inter", Arial, sans-serif'}

CSS = f"""
    .qwrap {{ position: absolute; left: {LEFT}px; right: {RIGHT}px; z-index: 6; display: flex; align-items: center;
             justify-content: center; }}
    .qcard {{ width: 100%; box-sizing: border-box; background: #fff; color: #0f1419; border-radius: 30px;
             padding: {PAD_TOP}px {PAD_X}px {PAD_BOTTOM}px; box-shadow: 0 26px 70px rgba(0,0,0,.5);
             transform-origin: 50% 50%; font-family: "Inter", "DejaVu Sans", Arial, sans-serif; text-align: left; }}
    .qhead {{ display: flex; align-items: center; gap: 20px; margin-bottom: 22px; }}
    .qava {{ width: 84px; height: 84px; border-radius: 50%; object-fit: cover; flex: none; background: #cfd9de; }}
    .qini {{ display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 700;
            font-size: 34px; background: #536471; }}
    .qname {{ display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 34px; line-height: 1.15; }}
    .qname svg {{ width: 32px; height: 32px; flex: none; }}
    .qhandle {{ margin-top: 4px; color: #536471; font-size: 27px; line-height: 1.2; }}
    .qtext {{ font-weight: 500; line-height: 1.3; white-space: pre-line; overflow-wrap: break-word; }}
    .qtr {{ margin-top: 22px; padding-top: 18px; border-top: 2px solid #e6ecf0; }}
    .qtrt {{ font-weight: 600; line-height: 1.3; overflow-wrap: break-word; }}
    .qfoot {{ margin-top: 20px; color: #536471; font-size: 25px; line-height: 1.2; }}
    .qcard.news {{ padding: 0 0 {PAD_BOTTOM}px; overflow: hidden; }}
    .qcard.news .qbody {{ padding: 26px {PAD_X}px 0; }}
    .qmast {{ display: flex; align-items: center; gap: 16px; height: {MAST_H}px; box-sizing: border-box;
             padding: 0 {PAD_X}px; }}
    .qmast.dark {{ background: var(--acc); color: #fff; }}
    .qmast.light {{ background: #fff; color: var(--acc); border-bottom: 4px solid var(--acc); }}
    .qmast img {{ width: 58px; height: 58px; border-radius: 12px; object-fit: cover; flex: none; background: #fff; }}
    .qword {{ font-weight: 800; font-size: 34px; letter-spacing: .5px; white-space: nowrap; overflow: hidden;
             text-overflow: ellipsis; }}
    .qcard.news .qheadline {{ font-weight: 800; line-height: 1.16; color: #111; }}
    .qcard.news .qtext {{ margin-top: 14px; color: #2b3238; }}"""

VERIFIED = ('<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="11" fill="#1d9bf0"/><path d="M7 12.4l3.3 3.3 '
            '6.7-7" stroke="#fff" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
            '</svg>')


def esc(s: str) -> str:
    return _html.escape(str(s), quote=True)


def lines(text: str, fs: float) -> int:
    per = max(8, math.floor(INNER_W / (CHAR_EM * fs)))
    return sum(max(1, math.ceil(len(p) / per)) for p in str(text).split("\n"))


def fit(card: dict, translation: str = "", room: int = BOTTOM - TOP_MIN) -> tuple[dict, bool]:
    """The largest type that fits the card into its room: {"text": px, "tr": px, "head": px}; False if even the
    smallest overflows (build.py warns: shorten the quote to its key sentence)."""
    avail = room - PAD_TOP - PAD_BOTTOM - (20 + 30 if card.get("date") or card.get("platform") else 0)
    if translation:
        avail -= 22 + 18
    news = card.get("kind") == "news"
    avail -= (MAST_H + 26 - PAD_TOP) if news else (84 + 22)
    best = None
    for fs in SIZES:
        tr, head = round(fs * 0.94), round(fs * 1.12)
        h = 0.0
        if news and card.get("headline"):
            h += lines(card["headline"], head) * head * 1.18
            if card.get("text"):
                h += 14 + lines(card["text"], fs) * fs * 1.3
        else:
            h += lines(card.get("text", ""), fs) * fs * 1.3
        if translation:
            h += lines(translation, tr) * tr * 1.3
        best = {"text": fs, "tr": tr, "head": head}
        if h <= avail:
            return best, True
    return best, False


def initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name.strip()) if p[:1].isalpha()]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


def domain(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/:?#]+)", str(url or ""))
    return m.group(1).lower() if m else ""


def source(card: dict) -> tuple[str, dict]:
    """The popular source a card quotes (quote_sources.json): its "source" key, the handle, the outlet or the
    person's name, or the url's domain; ("", {}) when it is none of them."""
    if card.get("source") in SOURCES:
        return card["source"], SOURCES[card["source"]]
    handle = str(card.get("handle") or "").lstrip("@").lower()
    who = str(card.get("outlet") or card.get("name") or "").casefold()
    dom = domain(card.get("url", ""))
    for key, s in SOURCES.items():
        if handle and handle in (str(s.get("x", "")).lower(), str(s.get("telegram", "")).lower()):
            return key, s
        if who and who in [n.casefold() for n in [s["name"], *s.get("aka", [])]]:
            return key, s
    for key, s in SOURCES.items():
        if dom and any(dom == d or dom.endswith("." + d) for d in s.get("domains", [])):
            return key, s
    return "", {}


def avatar_file(card: dict, proj: Path | None = None) -> Path | None:
    """The card's own "avatar" (in the project), else the source's from the library."""
    if card.get("avatar") and proj is not None and (proj / card["avatar"]).is_file():
        return proj / card["avatar"]
    key, _ = source(card)
    f = AVATARS / f"{key}.jpg"
    return f if key and f.is_file() else None


def card_html(card: dict, translation: str = "", avatar_src: str = "", room: int = BOTTOM - TOP_MIN) -> str:
    """The card itself (the inside of .qwrap)."""
    sz, _ = fit(card, translation, room)
    tr = (f'<div class="qtr"><div class="qtrt" style="font-size:{sz["tr"]}px">{esc(translation)}</div></div>'
          if translation else "")  # under a line, no «Перевод» label (owner 04.11: «и так понятно»)
    if card.get("kind") == "news":  # like the outlet's own page: its masthead, its headline face (owner 04.11)
        _, src = source(card)
        th = src.get("theme") or {"accent": "#111111", "mast": "light", "head": "serif"}
        name = card.get("outlet") or src.get("name", "")
        logo = f'<img src="{esc(avatar_src)}" alt="">' if avatar_src else ""
        mast = (f'<div class="qmast {th.get("mast", "light")}">{logo}<span class="qword" style="font-family:'
                f'{esc(HEAD_FONT.get(th.get("head"), HEAD_FONT["sans"]))}">{esc(name)}</span></div>')
        body = ((f'<div class="qheadline" dir="auto" style="font-size:{sz["head"]}px;font-family:'
                 f'{esc(HEAD_FONT.get(th.get("head"), HEAD_FONT["sans"]))}">{esc(card["headline"])}</div>'
                 if card.get("headline") else "")
                + (f'<div class="qtext" dir="auto" style="font-size:{sz["text"]}px">{esc(card["text"])}</div>'
                   if card.get("text") else ""))
        foot = " · ".join(esc(x) for x in (domain(card.get("url", "")), card.get("date")) if x)
        return (f'<div class="qcard news" style="--acc:{esc(th.get("accent", "#111111"))}">{mast}<div class="qbody">'
                f'{body}{tr}' + (f'<div class="qfoot">{foot}</div>' if foot else "") + "</div></div>")
    ava = (f'<img class="qava" src="{esc(avatar_src)}" alt="">' if avatar_src else
           f'<div class="qava qini">{esc(initials(card.get("name", "")))}</div>')
    who = " · ".join(esc(x) for x in (card.get("handle"), card.get("platform")) if x)
    head = (f'<div class="qhead">{ava}<div><div class="qname">{esc(card.get("name", ""))}'
            f'{VERIFIED if card.get("verified") else ""}</div><div class="qhandle">{who}</div></div></div>')
    date = f'<div class="qfoot">{esc(card["date"])}</div>' if card.get("date") else ""
    return (f'<div class="qcard post">{head}<div class="qtext" dir="auto" style="font-size:{sz["text"]}px">'
            f'{esc(card.get("text", ""))}</div>{tr}{date}</div>')


def top_under(hook_bottom: int) -> int:
    return max(TOP_MIN, hook_bottom + 24)


def clip(k: int, card: dict, translation: str, start: float, dur: float, avatar_src: str = "",
         top: int = TOP_MIN) -> tuple[str, str]:
    """HTML and timeline for the card of one shot in build.py's composition: in with a short rise, a gentle push-in
    across the shot."""
    html = (f'    <div id="qc{k}" class="clip qwrap" style="top:{top}px;height:{BOTTOM - top}px" data-start="{start}" '
            f'data-duration="{dur}" data-track-index="5">{card_html(card, translation, avatar_src, BOTTOM - top)}</div>')
    js = (f'  tl.from("#qc{k} .qcard", {{opacity: 0, y: 26, duration: 0.3, ease: "power2.out"}}, {start:.2f});\n'
          f'  tl.fromTo("#qc{k} .qcard", {{scale: 1.0}}, {{scale: {1 + PUSH:g}, duration: {dur:.2f}, ease: "none"}}, '
          f'{start:.2f});')
    return html, js


def foreign(text: str) -> bool:
    letters = [c for c in str(text) if c.isalpha()]
    return bool(letters) and sum("а" <= c.lower() <= "я" or c.lower() == "ё" for c in letters) < 0.5 * len(letters)


def problems(shots: list[dict]) -> list[str]:
    """What build.py warns about a story's cards."""
    out = []
    for i, s in enumerate(shots):
        for p in [s] + [e for e in s.get("extra") or [] if isinstance(e, dict)]:
            c = p.get("quote_card")
            if not c:
                continue
            words = c.get("headline") if c.get("kind") == "news" else c.get("text")
            if not words:
                out.append(f"shot {i}: quote_card without its words ({'headline' if c.get('kind') == 'news' else 'text'})")
                continue
            if not (c.get("outlet") if c.get("kind") == "news" else c.get("name")):
                out.append(f"shot {i}: quote_card without {'outlet' if c.get('kind') == 'news' else 'name'}")
            if foreign(" ".join(str(c.get(k, "")) for k in ("headline", "text"))) and not p.get("translation"):
                out.append(f"shot {i}: quote_card not in Russian without \"translation\" (README «Цитаты — карточкой»)")
            if not fit(c, p.get("translation", ""))[1]:
                out.append(f"shot {i}: quote_card text too long for the card — keep the key sentence")
            if not c.get("url"):
                out.append(f"shot {i}: quote_card without \"url\" of the original")
    return out


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=PT+Serif:wght@700&family=Playfair+Display:wght@700;800&family=Roboto+Condensed:wght@700;800&display=swap" rel="stylesheet">
<style>body{{margin:0;background:#0b0b0d}}#f{{position:relative;width:{W}px;height:{H}px;overflow:hidden;background:{bg}}}
#f .bg{{position:absolute;inset:-40px;background:url('{bgimg}') center/cover;filter:blur(28px) brightness(.7) saturate(.7)}}
.zone{{position:absolute;left:{L}px;right:{L}px;border:2px dashed rgba(255,255,255,.18)}}
.hook{{position:absolute;left:0;right:0;top:0;height:{hb}px;background:linear-gradient(#0a0a0c 82%,rgba(10,10,12,0));
color:#888;font:700 26px monospace;display:flex;align-items:flex-end;justify-content:center;padding-bottom:30px;box-sizing:border-box}}
.cap{{position:absolute;left:{L}px;right:{L}px;bottom:{cb}px;text-align:center}}
.cap span{{display:inline-block;font:900 66px Arial Black,Arial;color:#fff;background:rgba(0,0,0,.55);padding:8px 18px 10px;border-radius:10px}}
{css}
</style></head><body><div id="f"><div class="bg"></div><div class="hook">хук ролика</div>
<div class="qwrap" style="top:{top}px;height:{h}px;transform:scale({scale})">{card}</div>
<div class="cap"><span>титры</span></div></div></body></html>"""

CHROME = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "chromium",
          "chromium-browser"]


def preview(spec: dict, out: Path, bg: str = "", scale: float = 1.0) -> Path:
    """The card alone in a 1080x1920 frame (hook and caption zones marked) — for a look without a build."""
    out = out.resolve()
    tmp = Path(tempfile.mkdtemp())
    avatar = ""
    f = Path(spec["avatar_file"]) if spec.get("avatar_file") else avatar_file(spec["quote_card"])
    if f and f.is_file():
        avatar = f.resolve().as_uri()
    page = PAGE.format(W=W, H=H, L=LEFT, hb=TOP_MIN - 24, cb=H - 1500, top=TOP_MIN, h=BOTTOM - TOP_MIN, scale=scale,
                       bg="#1b1d22", bgimg=Path(bg).resolve().as_uri() if bg else "", css=CSS,
                       card=card_html(spec["quote_card"], spec.get("translation", ""), avatar))
    (tmp / "card.html").write_text(page, encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists() or shutil.which(c)), None)
    if not exe:
        sys.exit("no Chrome for the preview")
    subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={W},{H}",
                    "--virtual-time-budget=4000", f"--screenshot={out}", (tmp / "card.html").as_uri()],
                   check=True, capture_output=True)
    return out


def fetch_avatars(only: list[str] | None = None, force: bool = False) -> dict[str, str]:
    """The popular sources' pictures into avatars/<key>.jpg (200 px): the X or Telegram profile picture via unavatar.io
    (an outlet's is its logo), else the site's own icon. One request a source; what is there is kept."""
    import io
    import urllib.request

    from PIL import Image

    AVATARS.mkdir(exist_ok=True)
    out = {}
    for key, s in SOURCES.items():
        if only and key not in only:
            continue
        dst = AVATARS / f"{key}.jpg"
        if dst.is_file() and not force:
            out[key] = "kept"
            continue
        urls = ([f"https://unavatar.io/x/{s['x']}?fallback=false"] if s.get("x") else []) + \
               ([f"https://unavatar.io/telegram/{s['telegram']}?fallback=false"] if s.get("telegram") else []) + \
               [f"https://www.google.com/s2/favicons?domain={d}&sz=256" for d in s.get("domains", [])[:1]]
        for url in urls:
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                                            timeout=25) as r:
                    im = Image.open(io.BytesIO(r.read()))
                    im.load()
                if min(im.size) < 64:
                    continue
                bg = Image.new("RGB", im.size, "white")
                bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[3])
                side = min(bg.size)
                bg = bg.crop(((bg.width - side) // 2, (bg.height - side) // 2, (bg.width + side) // 2,
                              (bg.height + side) // 2)).resize((200, 200), Image.LANCZOS)
                bg.save(dst, quality=90)
                out[key] = url.split("?")[0]
                break
            except Exception as e:  # noqa: BLE001 — the next way, or none: the card shows initials / no logo
                out[key] = f"failed: {type(e).__name__}"
    return out


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "avatars":
        only = sys.argv[2].split(",") if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
        for k, v in fetch_avatars(only, "--force" in sys.argv).items():
            print(f"{k}: {v}")
        return
    if len(sys.argv) >= 4 and sys.argv[1] == "preview":
        bg = sys.argv[sys.argv.index("--bg") + 1] if "--bg" in sys.argv else ""
        specs = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        out = Path(sys.argv[3])
        for n, spec in enumerate(specs if isinstance(specs, list) else [specs], 1):
            dst = out if len(specs) == 1 else out.with_name(f"{out.stem}_{n}{out.suffix}")
            print(preview(spec, dst, spec.get("bg") or bg))
        return
    print(__doc__)


if __name__ == "__main__":
    main()
