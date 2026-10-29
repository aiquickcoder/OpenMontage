"""Real pictures made from data, not drawn by AI (owner 2026-10-29: no AI pictures): a map of the place and a fact card.

A shot in story.json can be, instead of a file:
  {"map": {"street": "улица Баумана", "city": "Казань", "label": "ул. Баумана"}, "beat": "..."}   # or "query": "..."
  {"src": "media/clean/x_3.clean.mp4", "card": {"label": "ПРИГОВОР", "big": "1 год 4 месяца", "small": "колонии"}}
A card is a compact plate laid over a real shot (owner 2026-10-29: «на весь экран не надо»), so a shot with "card"
keeps its own "src". build.py calls resolve() first: the files are made once (assets/visuals/, named by a hash of
the spec); a map shot's "src" points at its clip, a card shot gets "card_png". A card says only what the story
text says (a number, a term, a sum) — never a new fact.

The map: OpenStreetMap — Nominatim finds the place (street and city apart, so a street is not mistaken for the metro
station of the same name), tiles from tile.openstreetmap.org (cached in ~/.cache/openmontage/osm, a valid
User-Agent, a few dozen tiles a map — within the tile usage policy). Dark style, a smooth zoom from the city to the
street, a red pin and the place's name; «© OpenStreetMap» stays in the corner (ODbL needs it on the picture).

    .venv/bin/python addons/news/visuals.py card out.png "ПРИГОВОР" "1 год 4 месяца" "колонии общего режима"
    .venv/bin/python addons/news/visuals.py map out.mp4 "улица Баумана" "Казань" "ул. Баумана"
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

W, H = 1080, 760          # the picture box of the b-roll (build.py BOX_MAX)
RED = (227, 0, 27)        # the plates' red (#e3001b)
DARK = (11, 11, 13)       # the page background (#0b0b0d)
UA = "openmontage-chp-news/1.0 (local news shorts; contact: owner)"
CACHE = Path.home() / ".cache" / "openmontage"
BOLD = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]
REGULAR = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"]


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for f in BOLD if bold else REGULAR:
        if Path(f).is_file():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def fit(draw: ImageDraw.ImageDraw, text: str, width: int, start: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    size = start
    while size > 30 and draw.textlength(text, font=font(size, bold)) > width:
        size -= 4
    return font(size, bold)


# -- the fact card --------------------------------------------------------------------------------------------------
CARD_W = 900  # the plate over the picture: about the width of the headline plate, not the whole box


def card_overlay(spec: dict, out: Path) -> Path:
    """A transparent PNG: the dark plate with the red edge (like the headline plate), the label, the big line and
    the small one. build.py lays it at the bottom of the picture for the shot's length."""
    s = 2
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    fb = fit(probe, str(spec.get("big", "")), (CARD_W - 90) * s, 96 * s)
    fs, fl = font(40 * s, bold=False), font(30 * s)
    h = 34 * s + (56 * s if spec.get("label") else 0) + int(fb.size * 1.12) + (int(fs.size * 1.3) if spec.get("small") else 0) + 26 * s
    img = Image.new("RGBA", (CARD_W * s, h), (10, 10, 12, 224))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 12 * s, h), fill=RED + (255,))
    x, y = 44 * s, 26 * s
    if spec.get("label"):
        tw = d.textlength(spec["label"], font=fl)
        d.rectangle((x, y, x + tw + 28 * s, y + 46 * s), fill=RED + (255,))
        d.text((x + 14 * s, y + 6 * s), spec["label"], font=fl, fill="white")
        y += 56 * s
    d.text((x, y), str(spec.get("big", "")), font=fb, fill="white")
    y += int(fb.size * 1.12)
    if spec.get("small"):
        d.text((x, y), str(spec["small"]), font=fit(d, str(spec["small"]), (CARD_W - 90) * s, 40 * s, bold=False),
               fill=(225, 225, 225))
    img = img.resize((CARD_W, h // s), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


# -- the map ---------------------------------------------------------------------------------------------------------
def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read()


def geocode(spec: dict) -> tuple[float, float]:
    if "lat" in spec and "lon" in spec:
        return float(spec["lat"]), float(spec["lon"])
    if spec.get("street"):
        q = {"street": spec["street"], "city": spec.get("city", ""), "countrycodes": "ru"}
    else:
        q = {"q": spec["query"], "countrycodes": "ru"}
    key = json.dumps(q, ensure_ascii=False, sort_keys=True)
    cache = CACHE / "geocode.json"
    known = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else {}
    if key not in known:
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({**q, "format": "json", "limit": 5})
        found = json.loads(_get(url))
        time.sleep(1.1)  # Nominatim: at most one request a second
        if not found:
            sys.exit(f"map: Nominatim found nothing for {q} — give the street and the city, or lat/lon")
        streets = [f for f in found if f.get("class") == "highway"] if spec.get("street") else []
        best = (streets or found)[0]
        known[key] = [float(best["lat"]), float(best["lon"]), best.get("display_name", "")]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(known, ensure_ascii=False, indent=1), encoding="utf-8")
    lat, lon, _ = known[key]
    return lat, lon


def _tile(z: int, x: int, y: int) -> Image.Image:
    n = 2 ** z
    x %= n
    f = CACHE / "osm" / str(z) / str(x) / f"{y}.png"
    if not f.is_file():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(_get(f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"))
        time.sleep(0.05)
    return Image.open(io.BytesIO(f.read_bytes())).convert("RGB")


def _pixel(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 256 * 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def _view(lat: float, lon: float, z: int, w: int, h: int) -> Image.Image:
    """A w x h picture at integer zoom z with the point in the middle."""
    cx, cy = _pixel(lat, lon, z)
    x0, y0 = cx - w / 2, cy - h / 2
    img = Image.new("RGB", (w, h))
    for ty in range(int(y0 // 256), int((y0 + h) // 256) + 1):
        for tx in range(int(x0 // 256), int((x0 + w) // 256) + 1):
            img.paste(_tile(z, tx, ty), (int(tx * 256 - x0), int(ty * 256 - y0)))
    return img


def _dark(img: Image.Image) -> Image.Image:
    g = ImageOps.invert(ImageOps.grayscale(img))
    g = ImageEnhance.Contrast(g).enhance(1.35)
    g = ImageEnhance.Brightness(g).enhance(0.85)
    return ImageOps.colorize(g, black=(8, 10, 16), white=(190, 200, 215))


def map_clip(spec: dict, out: Path, seconds: float = 6.0, fps: int = 30) -> Path:
    lat, lon = geocode(spec)
    z0, z1 = float(spec.get("from", 11.5)), float(spec.get("to", 16.0))
    levels = {}
    n = int(seconds * fps)
    out.parent.mkdir(parents=True, exist_ok=True)
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                           "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    label = spec.get("label") or spec.get("street") or spec.get("query", "")
    for i in range(n):
        t = min(1.0, i / (0.7 * n))
        z = z0 + (z1 - z0) * (3 * t * t - 2 * t * t * t)  # ease in-out, then hold on the street
        base = min(int(math.floor(z)), int(z1))
        sc = 2 ** (z - base)
        w, h = int(W / sc) + 2, int(H / sc) + 2
        if base not in levels:
            levels[base] = _dark(_view(lat, lon, base, int(W * 1.05) + 2, int(H * 1.05) + 2))
        lv = levels[base]
        lw, lh = lv.size
        frame = lv.crop(((lw - w) // 2, (lh - h) // 2, (lw - w) // 2 + w, (lh - h) // 2 + h)).resize((W, H), Image.BILINEAR)
        d = ImageDraw.Draw(frame)
        cx, cy = W // 2, H // 2
        if t >= 1.0:  # a slow pulse once the street is in view
            r = 26 + 22 * ((i - 0.7 * n) / fps % 1.0)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=RED, width=4)
        d.ellipse((cx - 15, cy - 15, cx + 15, cy + 15), fill=RED, outline="white", width=4)
        if label and t > 0.35:
            f = font(40)
            tw = d.textlength(label, font=f)
            bx, by = min(cx + 34, W - tw - 60), cy - 34
            d.rectangle((bx, by, bx + tw + 36, by + 62), fill=(10, 10, 12))
            d.rectangle((bx, by, bx + 8, by + 62), fill=RED)
            d.text((bx + 22, by + 8), label, font=f, fill="white")
        d.text((W - 210, H - 34), "© OpenStreetMap", font=font(22, bold=False), fill=(170, 170, 175))
        ff.stdin.write(frame.tobytes())
    ff.stdin.close()
    if ff.wait() != 0:
        sys.exit("map: ffmpeg failed")
    return out


# -- story.json shots ------------------------------------------------------------------------------------------------
def resolve(proj: Path, story: dict) -> int:
    """Make the files of "card" / "map" shots (once) and point their "src" at them. Returns how many shots are made
    from data."""
    made = 0
    for st in story.get("stories") or [story]:
        for s in st.get("shots", []):
            for kind in ("map", "card"):
                if kind not in s:
                    continue
                if kind == "card" and not s.get("src"):
                    sys.exit(f"a fact card goes over a real shot: give the shot with {s['card']} its \"src\"")
                key = hashlib.sha1(json.dumps(s[kind], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:10]
                rel = Path("assets") / "visuals" / f"{kind}_{key}.{'png' if kind == 'card' else 'mp4'}"
                if not (proj / rel).is_file():
                    (card_overlay if kind == "card" else map_clip)(s[kind], proj / rel)
                s["card_png" if kind == "card" else "src"] = rel.as_posix()
                made += 1
    return made


if __name__ == "__main__":
    kind, out, *args = sys.argv[1:]
    if kind == "card":
        card_overlay({"label": args[0], "big": args[1], "small": args[2] if len(args) > 2 else ""}, Path(out))
    else:
        map_clip({"street": args[0], "city": args[1], "label": args[2] if len(args) > 2 else args[0]}, Path(out))
    print(out)
