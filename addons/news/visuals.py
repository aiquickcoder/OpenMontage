"""A real picture made from data, not drawn by AI (owner 2026-10-29: fewer AI pictures): a map of the place.

A shot in story.json can be, instead of a file:
  {"map": {"street": "улица Баумана", "city": "Казань", "label": "ул. Баумана"}, "beat": "..."}   # or "query": "..."
build.py calls resolve() first: the clip is made once (assets/visuals/, named by a hash of the spec) and the shot's
"src" points at it. A digest without the cat may put a map under a story's lead: "lead_shot": {"map": {...}}.
(Fact cards over the picture were tried and dropped by the owner the same day.)

The map: OpenStreetMap — Nominatim finds the place (street and city apart, so a street is not mistaken for the metro
station of the same name), tiles from tile.openstreetmap.org (cached in ~/.cache/openmontage/osm, a valid
User-Agent, a few dozen tiles a map — within the tile usage policy). Dark style, a smooth zoom from the city to the
street, a red pin and the place's name; «© OpenStreetMap» stays in the top corner (ODbL needs it on the picture;
the bottom is where a thesis goes).

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

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

W, H = 1080, 760          # the picture box of the b-roll (build.py BOX_MAX)
RED = (227, 0, 27)        # the plates' red (#e3001b)
DARK = (11, 11, 13)       # the page background (#0b0b0d)
MAP_VERSION = 2          # bumped when the drawing changes, so cached clips are made again (2: credit moved to the top)
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
        d.text((W - 210, 12), "© OpenStreetMap", font=font(22, bold=False), fill=(170, 170, 175))  # top: theses sit at the bottom
        ff.stdin.write(frame.tobytes())
    ff.stdin.close()
    if ff.wait() != 0:
        sys.exit("map: ffmpeg failed")
    return out


# -- story.json shots ------------------------------------------------------------------------------------------------
def resolve(proj: Path, story: dict) -> int:
    """Make the clips of "map" shots (once) and point their "src" at them. Returns how many there are."""
    made = 0
    for st in story.get("stories") or [story]:
        for s in [*st.get("shots", []), *([st["lead_shot"]] if st.get("lead_shot") else [])]:
            if "map" not in s:
                continue
            key = hashlib.sha1(json.dumps({**s["map"], "v": MAP_VERSION}, ensure_ascii=False,
                                          sort_keys=True).encode()).hexdigest()[:10]
            rel = Path("assets") / "visuals" / f"map_{key}.mp4"
            if not (proj / rel).is_file():
                (proj / rel).parent.mkdir(parents=True, exist_ok=True)
                map_clip(s["map"], proj / rel)
            s["src"] = rel.as_posix()
            made += 1
    return made


if __name__ == "__main__":
    kind, out, *args = sys.argv[1:]
    map_clip({"street": args[0], "city": args[1], "label": args[2] if len(args) > 2 else args[0]}, Path(out))
    print(out)
