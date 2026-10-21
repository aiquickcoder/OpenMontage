"""Narrator mode: voice the story with Fish Audio, transcribe it, and time every panel's beat to the real speech.

    python addons/mural/narrate.py projects/<slug>            # TTS + transcript + timing
    python addons/mural/narrate.py projects/<slug> --retime   # keep the audio, redo only transcript/timing

story.json must have:
  "narration": {"voice": "<fish reference_id>", "model": "s2.1-pro", "speed": 1.0,
                "style_tags": true}            # [sigh] / [pause] tags inside beat texts are spoken as emotion
  and every panel a "beat": the exact part of the narration it illustrates (panels in story order, together they
  are the whole narration). Emotion tags like [sigh] may sit inside a beat; they are removed for captions/timing.

Writes assets/narration.mp3, assets/narration_words.json, assets/timing.json:
  {"panels": [{"id", "start", "end", "words": [[word, start, end], ...]}], "speech_end": s, "duration": s}
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

TAG = re.compile(r"\[[^\]]*\]")
TAIL_S = 1.6  # hold after the last word (the camera settles, music fades)


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", TAG.sub(" ", text)).strip()


def norm(token: str) -> str:
    return re.sub(r"[^\wё]", "", token.lower().replace("ё", "е"))


def speak(story: dict, out: Path) -> None:
    from tools.audio.fish_audio_tts import FishAudioTTS

    n = story["narration"]
    text = " ".join(p["beat"].strip() for p in story["panels"])
    if not n.get("style_tags", True):
        text = plain(text)
    inputs = {"text": text, "model": n.get("model", "s2.1-pro"), "reference_id": n["voice"], "format": "mp3",
              "mp3_bitrate": 192, "output_path": str(out)}
    if n.get("speed") and float(n["speed"]) != 1.0:
        inputs["prosody"] = {"speed": float(n["speed"])}
    r = FishAudioTTS().execute(inputs)
    if not r.success:
        sys.exit(f"fish_audio_tts failed: {r.error}")


def transcribe(audio: Path) -> list[dict]:
    from tools.analysis.transcriber import Transcriber

    r = Transcriber().execute({"input_path": str(audio), "model_size": "small", "language": "ru"})
    if not r.success:
        sys.exit(f"transcriber failed: {r.error}")
    return [w for w in r.data["word_timestamps"] if w["word"].strip()]


def align(story: dict, words: list[dict]) -> dict:
    """Map each panel's beat onto the transcript (sequence alignment of normalised tokens)."""
    beats = [plain(p["beat"]).split() for p in story["panels"]]
    script = [norm(t) for b in beats for t in b]
    heard = [norm(w["word"]) for w in words]
    pos = {}  # script token index -> transcript index
    for blk in difflib.SequenceMatcher(None, script, heard, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            pos[blk.a + k] = blk.b + k
    ends, i = [], 0
    for b in beats:
        last = i + len(b) - 1
        j = next((pos[k] for k in range(last, i - 1, -1) if k in pos), None)
        if j is None:  # beat not heard at all: proportional fallback
            j = round((last + 1) / len(script) * len(words)) - 1
        ends.append(max(j, ends[-1] + 1 if ends else 0))
        i += len(b)
    ends[-1] = len(words) - 1
    panels, first = [], 0
    for p, end in zip(story["panels"], ends):
        seg = words[first:end + 1]
        panels.append({"id": p["id"], "words": [[w["word"].strip(), w["start"], w["end"]] for w in seg]})
        first = end + 1
    speech_end = words[-1]["end"]
    for k, p in enumerate(panels):  # a panel runs from its first word to the next panel's first word
        if k == 0:
            p["start"] = 0.0
        else:
            p["start"] = round(p["words"][0][1] - 0.15, 3) if p["words"] else round(panels[k - 1]["start"] + 0.5, 3)
            panels[k - 1]["end"] = p["start"]
    panels[-1]["end"] = round(speech_end + TAIL_S, 3)
    return {"panels": panels, "speech_end": speech_end, "duration": panels[-1]["end"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--retime", action="store_true", help="reuse assets/narration.mp3")
    a = ap.parse_args()
    proj = a.project.resolve()
    story = json.loads((proj / "story.json").read_text(encoding="utf-8"))
    A = proj / "assets"
    A.mkdir(parents=True, exist_ok=True)
    audio = A / "narration.mp3"
    if not (a.retime and audio.is_file()):
        speak(story, audio)
    words = transcribe(audio)
    (A / "narration_words.json").write_text(json.dumps(words, ensure_ascii=False, indent=1), encoding="utf-8")
    timing = align(story, words)
    (A / "timing.json").write_text(json.dumps(timing, ensure_ascii=False, indent=1), encoding="utf-8")
    for p in timing["panels"]:
        heard = " ".join(w[0] for w in p["words"])
        print(f"{p['id']}: {p['start']:.2f}-{p['end']:.2f}s  {heard[:90]}")
    print(f"speech {timing['speech_end']:.1f}s, video {timing['duration']:.1f}s")


if __name__ == "__main__":
    main()
