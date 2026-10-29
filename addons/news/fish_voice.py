"""The news voice: Fish Audio TTS, model Drama 3, voice «Энергичный Рассказчик» — the owner's pick (2026-10-29; before
it «Mita Miside» 6dc11f3f… from 28.10, and «тучный жаб» 806321a4…).

    .venv/bin/python addons/news/fish_voice.py "<text>" out.mp3 [--voice <model id>] [--model drama-3]

build.py uses it for "narration": {"engine": "fishaudio", "voice": "<fish.audio model id>", "model": "drama-3",
"direction": "..."}. Drama 3 takes delivery directions inline in square brackets (fish.audio/ru/drama-3): one goes
in front of the text: a calm, confident anchor without exclamations. build.py puts a [pause] between stories and
drops any other cue from the script: per-story tones and <stress> read out of place (owner, 2026-10-29; the four-take
A/B on the SPb opening chose drama-3, calm, no word-stress marks). Cues are stripped before a take is compared.
Abbreviations are read right as written (ЧП, МВД, СИЗО, ОПГ, ГАИ, СМС — checked 2026-10-28; spelled-out «га-и» broke).
Takes differ (the model is in preview): each take is transcribed and compared with the script, the best of up to
`tries` is kept, like the cedar voice (openrouter_voice.py). The bar is 85 %: the transcript writes numbers as digits
(«112», «28») and glues words («уфилармонии»), so a correct read scores ~88–92 %. Loudness -16 LUFS, 44.1 kHz.
Env: FISH_AUDIO_API_KEY (server: /opt/openmontage/.env; never printed). The server reaches api.fish.audio directly.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://api.fish.audio/v1/tts"
OWNER_VOICE = "48ec28a45588422b977a86460c11977f"  # https://fish.audio/ru/app/m/48ec28a45588422b977a86460c11977f/
# the owner's pick of four A/B takes on the SPb 28.10 opening (2026-10-29, «проба 1/4 — ок»): calm, no word-stress marks
DIRECTION = "уверенный ведущий новостей, естественная интонация, ровный темп, без восклицаний и театральности"
CUES = re.compile(r"\[[^\]]*\]|</?[a-z]+>")  # never spoken as words, so never compared


def speak_once(text: str, voice: str, model: str, direction: str, speed: float) -> bytes:
    body = {"text": f"[{direction}] {text}" if direction else text, "reference_id": voice, "format": "mp3",
            "mp3_bitrate": 192, "normalize": True, "latency": "normal"}
    if speed != 1.0:
        body["prosody"] = {"speed": speed}
    key = os.environ.get("FISH_AUDIO_API_KEY")
    if not key:
        sys.exit("FISH_AUDIO_API_KEY is not set (run with EnvironmentFile=/opt/openmontage/.env)")
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "model": model})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            msg = e.read()[:300].decode("utf-8", "replace")
            if e.code in (401, 402, 403):  # key or balance: retrying does not help
                sys.exit(f"fish.audio refused: HTTP {e.code} {msg}")
            print(f"fish.audio HTTP {e.code}: {msg}", flush=True)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"fish.audio: {e}", flush=True)
        time.sleep(5 * (attempt + 1))
    sys.exit("fish.audio did not answer")


def heard(mp3: Path) -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from addons.news.build import transcribe  # lazy: build imports this module too
    return " ".join(w["word"] if isinstance(w, dict) else str(w[0]) for w in transcribe(mp3))


def norm(s: str) -> list[str]:
    return [w for w in "".join(c.lower() if c.isalnum() else " " for c in s.replace("ё", "е")).split() if w]


def speak(text: str, out: Path, voice: str = OWNER_VOICE, model: str = "drama-3", direction: str = DIRECTION,
          speed: float = 1.0, tries: int = 3, min_match: float = 0.85) -> float:
    """Write out.mp3; returns how closely the spoken transcript matched the script (0..1)."""
    best: tuple[float, bytes] = (-1.0, b"")
    with tempfile.TemporaryDirectory() as tmp:
        for k in range(tries):
            audio = speak_once(text, voice, model, direction, speed)
            take = Path(tmp) / f"take{k}.mp3"
            take.write_bytes(audio)
            score = difflib.SequenceMatcher(None, norm(CUES.sub(" ", text)), norm(heard(take)), autojunk=False).ratio()
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                        str(take)], capture_output=True, text=True).stdout or 0)
            print(f"fish {model} voice={voice[:8]} try{k + 1}: {dur:.1f}s audio, match {score:.0%}", flush=True)
            if score > best[0]:
                best = (score, audio)
            if score >= min_match:
                break
        src = Path(tmp) / "best.mp3"
        src.write_bytes(best[1])
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", "loudnorm=I=-16:TP=-1.5", "-ar", "44100",
                        "-b:a", "192k", str(out)], check=True)
    return best[0]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("out", type=Path)
    ap.add_argument("--voice", default=OWNER_VOICE)
    ap.add_argument("--model", default="drama-3")
    a = ap.parse_args()
    print(f"match {speak(a.text, a.out, a.voice, a.model):.0%}")
