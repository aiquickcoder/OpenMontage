"""Voice-over through OpenRouter's audio-output chat models (openai/gpt-audio, openai/gpt-audio-mini).

These are chat models, not plain TTS: the system prompt pins them to reading the text verbatim, and the caller checks
the returned transcript against the script (retrying when the model paraphrased). Audio comes back streamed as
base64 PCM16 (24 kHz mono) chunks.

    .venv/bin/python addons/news/openrouter_voice.py "<text>" out.mp3 [--voice ash] [--model openai/gpt-audio-mini]

Env: OPENROUTER_API_KEY.
"""
from __future__ import annotations

import argparse
import base64
import difflib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

URL = "https://openrouter.ai/api/v1/chat/completions"
VOICES = ["alloy", "ash", "ballad", "cedar", "coral", "echo", "marin", "sage", "shimmer", "verse"]
STYLE = ("Ты диктор городских новостей. Прочитай текст пользователя слово в слово, ничего не добавляя, не пропуская и "
         "не комментируя. Голос серьёзный, собранный, без улыбки; темп новостной, чуть быстрее разговорного; "
         "чёткая дикция, короткие паузы на точках.")


def _env_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:  # same .env the rest of OpenMontage reads
        env = Path(__file__).resolve().parents[2] / ".env"
        if env.is_file():
            m = re.search(r"^OPENROUTER_API_KEY=(.+)$", env.read_text(), re.M)
            key = m.group(1).strip().strip('"') if m else ""
    if not key:
        sys.exit("OPENROUTER_API_KEY is not set (.env)")
    return key


def _norm(s: str) -> list[str]:
    return re.sub(r"[^\wё ]", " ", s.lower().replace("ё", "е")).split()


def speak_once(text: str, voice: str, model: str, style: str) -> tuple[bytes, str]:
    body = {"model": model, "modalities": ["text", "audio"], "audio": {"voice": voice, "format": "pcm16"},
            "stream": True, "temperature": 0.3,
            "messages": [{"role": "system", "content": style}, {"role": "user", "content": text}]}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {_env_key()}", "Content-Type": "application/json",
        "HTTP-Referer": "https://studio.luvael.ru", "X-Title": "OpenMontage news"})
    pcm, said = bytearray(), []
    with urllib.request.urlopen(req, timeout=180) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[5:])
            if chunk.get("error"):
                raise RuntimeError(str(chunk["error"])[:300])
            for ch in chunk.get("choices", []):
                audio = (ch.get("delta") or {}).get("audio") or {}
                if audio.get("data"):
                    pcm += base64.b64decode(audio["data"])
                if audio.get("transcript"):
                    said.append(audio["transcript"])
    return bytes(pcm), "".join(said)


def speak(text: str, out: Path, voice: str = "ash", model: str = "openai/gpt-audio-mini", style: str = STYLE,
          tries: int = 3, min_match: float = 0.97) -> float:
    """Write out.mp3; returns how closely the spoken transcript matched the script (0..1)."""
    best: tuple[float, bytes] = (-1.0, b"")
    for k in range(tries):
        pcm, said = speak_once(text, voice, model, style)
        score = difflib.SequenceMatcher(None, _norm(text), _norm(said), autojunk=False).ratio() if said else 0.0
        print(f"openrouter {model} voice={voice} try{k + 1}: {len(pcm) / 48000:.1f}s audio, match {score:.0%}", flush=True)
        if pcm and score > best[0]:
            best = (score, pcm)
        if score >= min_match:
            break
    if not best[1]:
        sys.exit("openrouter returned no audio")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "-",
                    "-af", "loudnorm=I=-16:TP=-1.5", "-ar", "44100", "-b:a", "192k", str(out)],
                   input=best[1], check=True)
    return best[0]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("out", type=Path)
    ap.add_argument("--voice", default="ash", choices=VOICES)
    ap.add_argument("--model", default="openai/gpt-audio-mini")
    a = ap.parse_args()
    print(f"match {speak(a.text, a.out, a.voice, a.model):.0%}")
