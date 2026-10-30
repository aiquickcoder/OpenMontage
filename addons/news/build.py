"""Local-news (ЧП) short: voice-over -> word timings -> 3-second b-roll cuts -> HyperFrames (plate, captions, music).

    .venv/bin/python addons/news/build.py projects/<slug>              # voice + b-roll + render + QA
    .venv/bin/python addons/news/build.py projects/<slug> --voice-only # voice + timings, prints how many shots it needs
    .venv/bin/python addons/news/build.py projects/<slug> --retime     # keep assets/narration.mp3, redo the rest

story.json (see addons/news/README.md and story.example.json):
  {"slug", "title", "tag": "ЧП · МОСКВА", "headline": "...", "date": "24 октября",
   "narration": {"engine": "piper"|"fish"|"openrouter", "voice": "<piper model | fish reference_id | ash, cedar, verse…>", "text": "..."},
   "shots": [{"src": "<video|image path>", "in": 3.0, "label": "ИЛЛЮСТРАЦИЯ", "credit": "Видео: @moscowach"}, ...],
   "music": {"src": "<file name in MUSIC_DIRS>", "volume": 0.12},
   "sound": {"sting": {"src": "news_jingle_radio.mp3", "volume": 0.45},          # TV-news ident under the greeting
             "ambience": {"src": "metro_moscow_station.mp3", "volume": 0.22},  # place bed under the whole b-roll
             "whoosh": {"src": "whoosh_transition.mp3", "volume": 0.35}}}      # anchor <-> b-roll cuts
  per shot: "sfx": {"src": "...", "volume": 0.4, "offset": 0} = one-shot at the cut; "audio": 0.35 = the clip's own
  sound (post footage is muted by default, owner 2026-10-28; "audio" > 0 brings one shot's sound back). SFX files live in SFX_DIRS.

Digest (several stories in one film): instead of "narration.text" + "shots", give
  "stories": [{"lead": "<the anchor's line before this story>", "text": "<voice-over>",
               "comment": "<the anchor's short reaction after it (no new facts)>", "headline", "shots",
               "sound": {"ambience": ...}, "date"?, "source"?}, ...]
The anchor reads intro + stories[0].lead, then after each story its comment and the next lead, then the outro; each
story gets its own b-roll window, plate (headline, "1 из 3") and ambience. One voice take covers the whole film.
Without "anchor.dir" a digest has no cat on screen (narrator mode, see narrator()): the pictures never stop.

"anchor": {"greeting_clip": {"src": "addons/news/anchor_cat/greet_hello.mp4", "speech": [0.78, 4.1], "words": 4}}
puts a generated (Veo) clip of the anchor saying the first n words over the opening, muted and re-timed to the voice.

Each shot holds SHOT_S seconds (the last one stretches to the end). A video shot without "in" continues where the
previous use of the same file stopped, so one source clip can feed several cuts. B-roll is pre-composed with ffmpeg
into one silent 1080x1920 clip (blurred fill behind the picture, slow push-in on stills); HyperFrames lays the
plate, chips, captions, voice and music over it.
"""
from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from addons.mural.build import FPS, H, W, find_music, render, word_cues  # noqa: E402
from addons.news import anchor as anc  # noqa: E402

SHOT_S = 3.0
CUT_MIN, SNAP_S = 1.8, 0.6  # a cut moves up to SNAP_S to land between words; no shot shorter than CUT_MIN
PIECE_S, LAST_PIECE_S = 3.35, 5.95  # cleaned post clips are 3.4 s, a story's last one 6 s (clean_footage.py)
TAIL_S = 1.2           # hold after the last word
CARD_ZOOM = 0.07       # a still's slow push-in (segment); "zoom": [from, to] on a cut overrides it
VOICE_AT = 0.3         # the voice starts this far into the composition; every timing below is composition time
BOX_Y = 930            # centre line of the picture box (the plate sits above it, captions below)
BOX_MIN, BOX_MAX = 760, 1000
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}
PIPER_MODELS = Path(os.environ.get("PIPER_MODELS", Path.home() / ".piper" / "models"))
TAG = re.compile(r"\[[^\]]*\]")    # delivery cues ([тревожно], [pause]): spoken by the voice, never captioned
MARK = re.compile(r"</?[a-z]+>")    # Drama 3 tags wrapping words (<stress>двенадцать</stress>): dropped in place


def sh(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"{cmd[0]} failed: {p.stderr.strip()[-600:]}")
    return p.stdout


def probe(src: Path) -> tuple[int, int, float]:
    d = json.loads(sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                       "stream=width,height:format=duration", "-of", "json", str(src)]))
    s = d["streams"][0]
    return int(s["width"]), int(s["height"]), float(d.get("format", {}).get("duration") or 0)


# -- voice -------------------------------------------------------------------------------------------
def plain(text: str) -> str:
    return re.sub(r"\s+", " ", MARK.sub("", TAG.sub(" ", text))).strip()


def norm(token: str) -> str:
    return re.sub(r"[^\wё]", "", token.lower().replace("ё", "е"))


def stories(story: dict) -> list[dict]:
    """The film's stories: a digest's "stories", else the story itself (a one-story film)."""
    return story["stories"] if story.get("stories") else [story]


def narrator(story: dict) -> bool:
    """A digest without the cat on screen (no "anchor.dir"; owner 2026-10-29, a test without the cat): one voice reads
    the greeting, the leads, the stories and the outro over pictures that never stop — the greeting over a still of
    every story (a teaser), each lead over its story's first picture, the outro over the last one. A story's
    "lead_shot" (a map of the place: {"map": {...}}, or any "src") goes under its lead instead. The cat's comments
    after the stories are not read: they were his reactions."""
    return bool(story.get("stories")) and not (story.get("anchor") or {}).get("dir")


def parts(story: dict) -> list[dict]:
    """What is read, in order: anchor lines and story voice-overs ({"kind": "anchor"|"story", "text", "i"})."""
    a = story.get("anchor") or {}
    out = []
    for i, s in enumerate(stories(story)):
        lead = s.get("lead", "") if story.get("stories") else ""
        if i == 0:
            lead = f'{a.get("intro", "")} {lead}'
        out.append({"kind": "anchor", "role": "lead", "text": lead.strip(), "i": i})
        out.append({"kind": "story", "role": "story", "text": (s.get("text") or story["narration"]["text"]).strip(),
                    "i": i})
        if story.get("stories") and not narrator(story):  # the anchor's own short reaction, before the next lead
            out.append({"kind": "anchor", "role": "comment", "text": s.get("comment", "").strip(), "i": i})
    out.append({"kind": "anchor", "role": "outro", "text": a.get("outro", "").strip(), "i": None})
    return [p for p in out if p["text"]]


def full_text(story: dict) -> str:
    return " ".join(p["text"] for p in parts(story))


def voice_text(story: dict) -> str:
    """What Fish Drama 3 reads: the plain script and a [pause] before every next lead and the outro, so a digest does
    not run on as one block (owner, 2026-10-28). Tone cues and <stress> in a text are dropped: the owner heard them
    as out of place (2026-10-29, «эмоциональные усиления уберём») — the calm direction in fish_voice.py is enough."""
    out = []
    for p in parts(story):
        if out and p["role"] in ("lead", "outro"):
            out.append("[pause]")
        out.append(plain(p["text"]))
    return " ".join(out)


def windows(timing: dict) -> list[list[float]]:
    """One b-roll window per story (timing.json from before digests has only "broll")."""
    return timing.get("windows") or [timing["broll"]]


def text_windows(timing: dict) -> list[list[float]]:
    """Each story's voice-over, the phrases are cut in it: its b-roll window, except in narrator mode."""
    return timing.get("text") or windows(timing)


def narrator_windows(story: dict, words: list, duration: float, text: list[list[float]]) -> dict:
    """Narrator mode: story i's pictures run from its lead (the first from 0) to the next lead, the last to the end;
    "text" keeps the voice-over windows, "intro" the greeting before the first lead."""
    starts = [0.0] + [t1 for _, t1 in text[:-1]]
    out = {"text": text, "windows": [[s, e] for s, e in zip(starts, starts[1:] + [duration])]}
    n = len(plain((story.get("anchor") or {}).get("intro", "")).split())
    if n and n < len(words):
        out["intro"] = [0.0, round(min(max(words[n - 1][2], words[n][1] - 0.1), text[0][0]), 3)]
    return out


def story_windows(story: dict, words: list, duration: float) -> list[list[float]]:
    """Each story's b-roll runs from just after the anchor's preceding line to just before the next one. A "cold_open"
    film starts on the first story's pictures, not on the cat (owner 2026-10-30, the TikTok review: «первый кадр должен
    бить самым жёстким ЧП»): its hook line is read over them."""
    out, k = [], 0
    for p in parts(story):
        n = len(plain(p["text"]).split())
        if p["kind"] == "story":
            b0 = 0.0 if story.get("cold_open") and not out else round(words[k - 1][2] + 0.15, 3) if k else 0.0
            b1 = round(words[k + n][1] - 0.1, 3) if k + n < len(words) else duration
            out.append([b0, b1])
        k += n
    return out


def speak(n: dict, out: Path) -> None:
    engine = n.get("engine", "piper")
    if engine == "openrouter":
        from addons.news.openrouter_voice import STYLE
        from addons.news.openrouter_voice import speak as or_speak

        or_speak(n["text"], out, n.get("voice", "ash"), n.get("model", "openai/gpt-audio-mini"), n.get("style", STYLE))
        return
    if engine == "fishaudio":  # the news voice («Энергичный Рассказчик»), Fish Audio Drama 3 (addons/news/fish_voice.py)
        from addons.news.fish_voice import DIRECTION, OWNER_VOICE
        from addons.news.fish_voice import speak as fish_speak

        try:
            fish_speak(n["text"], out, n.get("voice", OWNER_VOICE), n.get("model", "drama-3"),
                       n.get("direction", DIRECTION), float(n.get("speed", 1.0)))
            return
        except SystemExit as e:  # key, balance or outage: the morning film must still come out
            if not n.get("fallback"):
                raise
            print(f"fishaudio failed ({e}); falling back to {n['fallback']}", flush=True)
            return speak({**n["fallback"], "text": plain(n["text"])}, out)
    if engine == "fish":
        from tools.audio.fish_audio_tts import FishAudioTTS

        inputs = {"text": n["text"], "model": n.get("model", "s2.1-pro"), "reference_id": n["voice"],
                  "format": "mp3", "mp3_bitrate": 192, "output_path": str(out)}
        if n.get("speed") and float(n["speed"]) != 1.0:
            inputs["prosody"] = {"speed": float(n["speed"])}
        r = FishAudioTTS().execute(inputs)
        if not r.success:
            sys.exit(f"fish_audio_tts failed: {r.error}")
        return
    model = PIPER_MODELS / f"{n.get('voice', 'ru_RU-irina-medium')}.onnx"
    if not model.is_file():
        sys.exit(f"piper model {model} missing (see addons/news/README.md, «Голос»)")
    piper = shutil.which("piper") or str(Path(sys.executable).parent / "piper")
    wav = out.with_suffix(".wav")
    p = subprocess.run([piper, "-m", str(model), "-f", str(wav), "--length-scale", str(n.get("length_scale", 0.92)),
                        "--sentence-silence", str(n.get("sentence_silence", 0.25))],
                       input=plain(n["text"]), capture_output=True, text=True)
    if p.returncode != 0 or not wav.is_file():
        sys.exit(f"piper failed: {p.stderr.strip()[-400:]}")
    sh(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-af", "loudnorm=I=-16:TP=-1.5", "-ar", "44100",
        "-b:a", "192k", str(out)])
    wav.unlink()


def transcribe(audio: Path) -> list[dict]:
    from tools.analysis.transcriber import Transcriber

    r = Transcriber().execute({"input_path": str(audio), "model_size": "small", "language": "ru"})
    if not r.success:
        sys.exit(f"transcriber failed: {r.error}")
    return [w for w in r.data["word_timestamps"] if w["word"].strip()]


def script_words(text: str, heard: list[dict]) -> list[list]:
    """Captions show the SCRIPT's words (whisper misspells names) at the times they were heard."""
    script = plain(text).split()
    # a dash or a lone quote normalises to "", and "" would match any empty heard token far away (a 28 s «—» caption,
    # 2026-10-28): such tokens never match and are spread between their heard neighbours like an unheard word
    a = [norm(t) or f"\0{i}" for i, t in enumerate(script)]
    b = [norm(w["word"]) or "\1" for w in heard]
    pos = {}
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            pos[blk.a + k] = blk.b + k
    out, k = [], 0
    while k < len(script):
        if k in pos:
            w = heard[pos[k]]
            out.append([script[k], w["start"], w["end"]])
            k += 1
            continue
        j = k  # run of unheard script words: spread them between the neighbours that were heard
        while j < len(script) and j not in pos:
            j += 1
        t0 = out[-1][2] if out else 0.0
        t1 = heard[pos[j]]["start"] if j < len(script) else (heard[-1]["end"] if heard else t0 + 0.4 * (j - k))
        step = max(t1 - t0, 0.2 * (j - k)) / (j - k)
        for i in range(k, j):
            out.append([script[i], round(t0 + (i - k) * step, 3), round(t0 + (i - k + 1) * step, 3)])
        k = j
    return out


def voice(proj: Path, story: dict, retime: bool) -> dict:
    A = proj / "assets"
    A.mkdir(parents=True, exist_ok=True)
    audio = A / "narration.mp3"
    text = full_text(story)
    if not (retime and audio.is_file()):
        engine = story["narration"].get("engine")
        # OpenRouter's chat voices would read a [cue] aloud: they get the plain script
        said = voice_text(story) if engine == "fishaudio" else plain(text) if engine == "openrouter" else text
        speak({**story["narration"], "text": said}, audio)
    words = [[w, round(a + VOICE_AT, 3), round(b + VOICE_AT, 3)] for w, a, b in script_words(text, transcribe(audio))]
    speech_end = float(words[-1][2])
    duration = round(speech_end + TAIL_S, 2)
    ws = story_windows(story, words, duration)
    timing = {"words": words, "speech_end": speech_end, "duration": duration, "windows": ws,
              "broll": [ws[0][0], ws[-1][1]]}
    if narrator(story):
        timing.update(narrator_windows(story, words, duration, ws))
    (A / "timing.json").write_text(json.dumps(timing, ensure_ascii=False, indent=1), encoding="utf-8")
    return timing


# -- b-roll ------------------------------------------------------------------------------------------
BEAT_MIN, BEAT_MAX, BEAT_LEAD = 1.4, 3.0, 0.12  # a phrase shot lasts 1.4–3 s and cuts in just before its first word
BEAT_HARD = 4.2  # a whole phrase up to here rather than a torn one; clean_footage.py cleans pieces as long as phrases
JOINS = {"и", "а", "но", "или", "когда", "чтобы", "потому", "поэтому", "где", "куда", "который", "которая", "которое",
         "которые", "которого", "хотя", "если", "однако", "—"}  # not «пока»: mostly «пока не установлена»
PREPS = {"в", "во", "на", "у", "с", "со", "по", "к", "ко", "из", "за", "от", "до", "при", "для", "без", "под", "над", "о",
         "об", "про", "через", "около", "возле", "после", "перед", "между", "из-за", "не", "ни"}
NUMERAL = re.compile(r"^(одн|один|два$|две$|двух|двум|тр[её]х|три$|трём|четыр|пят[ьиа]|шест[ьиа]|сем[ьи]|восем|девят|"
                     r"десят|двадцат|тридцат|сорок|девяност|сто$|ста$|двест|двухсот|трист|четырест|пятьсот|тысяч|"
                     r"миллион|миллиард|полтор|нескольк|\d)")  # «двадцати | шести» must stay one number


def story_words(story: dict, words: list) -> list[list]:
    """The timed words of each story's voice-over (not the anchor's lines)."""
    out, k = [], 0
    for p in parts(story):
        n = len(plain(p["text"]).split())
        if p["kind"] == "story":
            out.append(words[k:k + n])
        k += n
    return out


def beats(ws: list, window: list[float]) -> list[dict]:
    """One story's voice-over cut into phrases, one shot each, so the picture changes as its phrase begins (owner
    2026-10-29: the sentence and the picture must go together, and change often). Sentences first; a long one is split
    at a comma, dash, colon or joining word nearest its middle, so a phrase runs BEAT_MIN..BEAT_MAX s; a short one is
    merged into a neighbour. A phrase lasts from just before its first word (BEAT_LEAD, in the pause) to the next
    phrase (the first from the window start, the last to its end), at most BEAT_HARD (the last LAST_PIECE_S):
    clean_footage.py reads assets/beats.json and cleans each post-clip piece as long as its phrase."""
    if not ws:
        return [{"start": window[0], "end": window[1], "text": ""}]
    # the cut: BEAT_LEAD before the phrase's first word, but never inside the word before it
    cut = lambda i: min(ws[i][1], max(ws[i - 1][2], ws[i][1] - BEAT_LEAD))  # noqa: E731
    start = lambda i: window[0] if i == 0 else cut(i)  # noqa: E731
    end = lambda j: window[1] if j == len(ws) else cut(j)  # noqa: E731
    dur = lambda r: end(r[1]) - start(r[0])  # noqa: E731
    cap = lambda r: LAST_PIECE_S if r[1] == len(ws) else BEAT_HARD  # noqa: E731
    sentences, i = [], 0
    for j, w in enumerate(ws, 1):
        if w[0].rstrip("»\")")[-1:] in ".!?…":
            sentences.append((i, j))
            i = j
    if i < len(ws):
        sentences.append((i, len(ws)))

    def split(r: tuple[int, int]) -> list[tuple[int, int]]:
        if r[1] - r[0] < 2 or (dur(r) <= BEAT_MAX and dur(r) <= cap(r)):
            return [r]
        mid = (start(r[0]) + end(r[1])) / 2

        def cost(k: int) -> float:  # where Russian breaks naturally, then nearness to the middle
            prev, nxt = ws[k - 1][0].lower().strip("«("), ws[k][0].lower().strip("«(")
            quoted = sum(w[0].count("«") - w[0].count("»") for w in ws[:k]) > 0  # inside «Хади Такташ»
            if prev in PREPS or prev in JOINS or quoted or NUMERAL.match(prev):  # never part a preposition,
                kind = 2.0                                     # «не», a conjunction, a quoted name or a number
            elif prev[-1:] in ",;:—" or nxt in JOINS:
                kind = 0.0
            elif nxt in PREPS:
                kind = 0.25
            else:
                kind = 0.5
            return kind + abs(start(k) - mid) / dur(r)

        ok = [k for k in range(r[0] + 1, r[1]) if dur((r[0], k)) >= BEAT_MIN and dur((k, r[1])) >= BEAT_MIN]
        if not ok:
            if dur(r) <= cap(r):
                return [r]
            ok = list(range(r[0] + 1, r[1]))  # over a clip piece: split anyway
        k = min(ok, key=cost)
        # a whole phrase beats a torn one: keep it when the only breaks are forbidden ones (and it fits a clip piece),
        # or when the break is a plain one between words and the phrase is only a little long
        if (cost(k) >= 2.0 and dur(r) <= cap(r)) or (cost(k) >= 0.5 and dur(r) <= min(BEAT_MAX + 0.3, cap(r))):
            return [r]
        return split((r[0], k)) + split((k, r[1]))

    out = [b for r in sentences for b in split(r)]
    merged = True
    while merged and len(out) > 1:
        merged = False
        for n, r in enumerate(out):
            if dur(r) >= BEAT_MIN:
                continue
            pairs = [m for m in (n - 1, n + 1) if 0 <= m < len(out)]
            pairs = [m for m in pairs if dur((out[min(n, m)][0], out[max(n, m)][1])) <= min(BEAT_MAX + 0.3, cap((out[min(n, m)][0], out[max(n, m)][1])))]
            if pairs:
                m = min(pairs, key=lambda m: dur(out[m]))
                a, b = min(n, m), max(n, m)
                out[a:b + 1] = [(out[a][0], out[b][1])]
                merged = True
                break
    return [{"start": round(start(i), 3), "end": round(end(j), 3), "text": " ".join(w[0] for w in ws[i:j])}
            for i, j in out]


def shot_beats(shots: list, ws: list, window: list[float]) -> Optional[list[dict]]:
    """The phrases the shots were planned for ("beat": the first words of its phrase), found again in this take's
    words: a film voiced anew (another voice, another tempo) keeps every cut on its phrase. None when a shot has no
    beat or the beats are not found in order from the first word."""
    if not shots or not ws or not all(s.get("beat") for s in shots):
        return None
    toks, at = [norm(w[0]) for w in ws], []

    def opens(j: int, want: list[str]) -> bool:  # a beat's last word may be cut short («пострадавш»)
        got = [t for t in toks[j:j + 6] if t][:len(want)]
        return len(got) == len(want) and got[:-1] == want[:-1] and got[-1].startswith(want[-1])

    for s in shots:
        want = [t for t in (norm(x) for x in plain(s["beat"]).split()) if t][:3]
        k = at[-1] + 1 if at else 0
        j = next((j for j in range(k, len(toks)) if want and opens(j, want)), None)
        if j is None or (not at and any(toks[:j])):
            return None
        at.append(j)
    cut = lambda i: window[0] if i == 0 else min(ws[i][1], max(ws[i - 1][2], ws[i][1] - BEAT_LEAD))  # noqa: E731
    edges = [cut(i) for i in at] + [window[1]]
    return [{"start": round(a, 3), "end": round(b, 3), "text": " ".join(w[0] for w in ws[i:j])}
            for a, b, i, j in zip(edges, edges[1:], at, at[1:] + [len(ws)])]


def story_beats(story: dict, timing: dict) -> list[list[dict]]:
    return [shot_beats(s.get("shots", []), ws, win) or beats(ws, win)
            for s, ws, win in zip(stories(story), story_words(story, timing["words"]), text_windows(timing))]


def cut_points(window: list[float], n: int, words: list, last_cap: float) -> list[float]:
    """The n-1 cuts inside one story window: evenly spaced, then each moved to the nearest boundary between two
    spoken words within SNAP_S — a pause or a sentence end preferred — so no cut falls inside a word (owner,
    2026-10-28). Every shot stays CUT_MIN..PIECE_S long (the last one up to last_cap): a cleaned clip is no longer."""
    t0, t1 = window
    step, bounds = (t1 - t0) / n, []
    for (w, _, b), (_, c, _) in zip(words, words[1:]):
        if t0 < b and c < t1:
            end = w.rstrip("»\")")[-1:]
            bonus = min(c - b, 0.4) * 0.5 + (0.25 if end in ".!?…" else 0.1 if end in ",;:—" else 0.0)
            bounds.append(((b + c) / 2, bonus))
    cuts, prev = [], t0
    for k in range(1, n):
        target, left = t0 + k * step, n - k
        lo = max(prev + CUT_MIN, t1 - (left - 1) * PIECE_S - last_cap)
        hi = min(prev + PIECE_S, t1 - left * CUT_MIN)
        near = [(abs(t - target) - bonus, t) for t, bonus in bounds
                if max(lo, target - SNAP_S) <= t <= min(hi, target + SNAP_S)]
        # a long, slowly read word can cover the whole ±SNAP_S: then the nearest boundary the shot lengths allow
        near = near or [(abs(t - target), t) for t, _ in bounds if lo <= t <= hi]
        cut = min(near)[1] if near else (min(max(target, lo), hi) if lo <= hi else target)
        cuts.append(round(cut, 3))
        prev = cut
    return cuts


def slots(story: dict, window: list[float], proj: Path, words: list = (), phrases: list = (),
          strict: bool = True) -> list[dict]:
    t0, duration = window[0], window[1] - window[0]
    shots, n = story["shots"], max(1, math.ceil(duration / SHOT_S - 0.15))
    if phrases and len(shots) >= len(phrases):  # a shot per phrase: cut as each phrase begins
        n = len(phrases)
        edges = [*(b["start"] for b in phrases), window[1]]
        for i, (s, b) in enumerate(zip(shots, phrases)):
            if s.get("beat") and not plain(b["text"]).lower().startswith(plain(s["beat"]).lower()[:12]):
                print(f"warning: shot {i} is for «{s['beat']}», the phrase there is «{b['text'][:40]}»", flush=True)
    else:
        if phrases:
            print(f"warning: {len(shots)} shots for {len(phrases)} phrases — cuts fall in pauses, not on phrases",
                  flush=True)
        if len(shots) < n:
            print(f"warning: {len(shots)} shots for {n} slots — the last shot is held longer", flush=True)
        edges = [t0, *cut_points(window, n, list(words), LAST_PIECE_S if len(shots) <= n else PIECE_S), window[1]]
    used: dict[str, float] = {}
    out, t = [], 0.0
    for i, s in enumerate(shots[:n]):
        d = round((edges[i + 1] if i < min(n, len(shots)) - 1 else window[1]) - edges[i], 3)
        src = (proj / s["src"]).resolve() if not Path(s["src"]).is_absolute() else Path(s["src"])
        if not src.is_file():
            if strict:
                sys.exit(f"shot {i}: {src} not found")
            out.append({**s, "path": None, "start": round(t0 + t, 3), "dur": d, "video": False})  # storyboard only
            t += d
            continue
        item = {**s, "path": src, "start": round(t0 + t, 3), "dur": d, "video": src.suffix.lower() in VIDEO_EXT}
        if item["video"]:
            w, h, length = probe(src)
            a = float(s["in"]) if "in" in s else used.get(str(src), 0.3)
            if a + d > length:  # clip too short for this cut: loop back to its start
                a = max(0.0, min(0.3, length - d))
            if d > length + 0.1:  # segment() holds the last frame
                print(f"warning: shot {i} {src.name} is {length:.1f}s for a {d:.1f}s cut — its last frame is held "
                      f"{d - length:.1f}s", flush=True)
            item["in"] = round(a, 3)
            used[str(src)] = a + d
        out.append(item)
        t += d
    return out


def still_of(proj: Path, it: dict, last: bool = False) -> Optional[dict]:
    """A cut as a still with the same framing (crop, the Flow sparkle by "label"): a picture is itself, a clip gives
    the frame it starts on (or ends on)."""
    if not it.get("path"):
        return None
    keep = {k: it[k] for k in ("src", "crop", "label", "credit") if k in it}
    if not it["video"]:
        return {**keep, "path": it["path"], "video": False}
    _, _, length = probe(it["path"])
    t = max(0.0, min(it["in"] + it["dur"], length) - 0.1) if last else it["in"]
    dst = proj / "assets" / "cards" / f"{it['path'].stem}_{t:.2f}.jpg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(it["path"]), "-frames:v", "1", "-q:v", "2", str(dst)])
    return {**keep, "path": dst, "video": False}


def cards(proj: Path, story: dict, timing: dict, per: list[list[dict]]) -> list[list[dict]]:
    """Narrator mode: what is read between the voice-overs gets pictures too. The greeting runs over a still of every
    story (a teaser of the film); a lead over its story's first picture — a clip's first frame that pulls back to the
    clip's framing and starts to move, or the picture itself, held longer (or the story's "lead_shot"); the outro over
    the last picture."""
    out = []
    for w, its in enumerate(per):
        (v0, v1), (t0, t1) = windows(timing)[w], text_windows(timing)[w]
        its, pre = list(its), []
        if w == 0 and timing.get("intro"):
            i1 = timing["intro"][1] if t0 - timing["intro"][1] > 0.4 else t0  # no lead of its own: up to the story
            teaser = [f for f in (still_of(proj, p[0]) for p in per if p) if f]
            k = max(1, min(len(teaser), int((i1 - v0) / 0.7)))
            e = [round(v0 + (i1 - v0) * j / k, 3) for j in range(k + 1)]
            pre = [{**f, "start": a, "dur": round(b - a, 3), "card": "intro"} for f, a, b in zip(teaser, e, e[1:])]
            v0 = i1
        lead = stories(story)[w].get("lead_shot") or {}
        src = (proj / lead["src"]).resolve() if lead.get("src") else None
        if src and src.is_file() and t0 - v0 > 0.05:
            video = src.suffix.lower() in VIDEO_EXT
            pre.append({**lead, "path": src, "video": video, "in": float(lead.get("in", 0.0)), "start": round(v0, 3),
                        "dur": round(t0 - v0, 3), "card": "lead"})
        elif its and t0 - v0 > 0.05:
            if its[0]["video"] and (f := still_of(proj, its[0])):
                pre.append({**f, "start": round(v0, 3), "dur": round(t0 - v0, 3), "card": "lead",
                            "zoom": [1 + CARD_ZOOM, 1.0]})
            else:
                its[0] = {**its[0], "start": round(v0, 3), "dur": round(its[0]["dur"] + t0 - v0, 3)}
        if its and v1 - t1 > 0.05:
            if its[-1]["video"] and (f := still_of(proj, its[-1], last=True)):
                its.append({**f, "start": round(t1, 3), "dur": round(v1 - t1, 3), "card": "outro"})
            else:
                its[-1] = {**its[-1], "dur": round(its[-1]["dur"] + v1 - t1, 3)}
        out.append(pre + its)
    return out


def film_items(proj: Path, story: dict, timing: dict, phrases: list, strict: bool = True) -> list[dict]:
    """Every cut of the film in order; "w" is its story's window."""
    per = [slots(s, win, proj, timing["words"], phrases[w], strict)
           for w, (s, win) in enumerate(zip(stories(story), text_windows(timing)))]
    if narrator(story):
        per = cards(proj, story, timing, per)
    return [{**it, "w": w} for w, its in enumerate(per) for it in its]


def picture_warnings(story: dict) -> list[str]:
    """Pictures that break the rules (owner 2026-10-29: a metro station photo of story 1 under the court story 4, one
    picture four times in a row): a still more than twice in a story or twice in a row, post media in two stories."""
    out, where = [], {}
    for w, st in enumerate(stories(story)):
        srcs = [(s.get("src_orig") or s["src"]) + (f"@{s['crop']}" if s.get("crop") else "")  # a crop: another view
                for s in st.get("shots", [])]
        stills = [x for x in srcs if Path(x.split("@")[0]).suffix.lower() not in VIDEO_EXT]
        out += [f"story {w + 1}: {Path(x).name} is shown {stills.count(x)} times — at most twice"
                for x in sorted(set(stills)) if stills.count(x) > 2]
        out += [f"story {w + 1}: {Path(x).name} twice in a row" for x, y in zip(srcs, srcs[1:])
                if x == y and x in stills]
        for x in srcs:
            if x.startswith("media/"):
                where.setdefault(x.split("@")[0], set()).add(w + 1)
    out += [f"{Path(x).name} is shown in stories {sorted(ws)} — a picture belongs to its own story"
            for x, ws in where.items() if len(ws) > 1]
    for w, st in enumerate(stories(story)):  # footage stays whole (owner 2026-10-30: «футажи … остаются цельными»)
        seq = [(s.get("src_orig") or s.get("src", ""), float(s.get("in_orig", s.get("in", 0)))) for s in st.get("shots", [])]
        clips = [(a, t) for a, t in seq if Path(a).suffix.lower() in VIDEO_EXT and "visuals/" not in a]
        # two long pieces of a clip in order (skipping a dark or dull part) are an edit; three in a row are chopping
        out += [f"story {w + 1}: {Path(a).name} is cut into pieces — let the clip run as one shot over its phrases"
                for (a, _), (b, _), (c, _) in zip(seq, seq[1:], seq[2:]) if a == b == c and (a, _) in clips]
        out += [f"story {w + 1}: {Path(a).name} jumps back in time ({ta:g} s after {tb:g} s) — keep its order"
                for i, (a, ta) in enumerate(clips) for b, tb in clips[:i] if a == b and ta < tb]
    return out


def unused_post_media(proj: Path, story: dict) -> list[str]:
    """Post media checked for watermarks (media/wm.json) but shown nowhere: the post's own picture goes first (owner
    2026-10-29; the Kazan agent cleaned the Baumana photo and drew five illustrations instead)."""
    wm = proj / "media" / "wm.json"
    if not wm.is_file():
        return []
    used = {Path(s.get("src_orig") or s["src"]).name for st in stories(story) for s in st.get("shots", [])}
    return [f"{name} from the post is not shown in any shot — the post's own picture goes first"
            for name in json.loads(wm.read_text()) if name not in used]


CITIES = json.loads((Path(__file__).resolve().parent / "cities.json").read_text(encoding="utf-8"))


def ai_budget(story: dict) -> tuple[int, int, float]:
    """(AI-illustration shots, all shots, the allowed share): cities.json ai_max_share, or the story's own
    "ai_max_share". 0 since 2026-10-29 — views fell as the AI share grew (15% → 100%: 1388 → 10 views)."""
    shots = [s for st in stories(story) for s in st.get("shots", [])]
    ai = sum(1 for s in shots if s.get("label") or s["src"].startswith("illustrations/"))
    limit = story.get("ai_max_share", CITIES.get(story.get("city", ""), {}).get("ai_max_share", 1))
    return ai, len(shots), float(limit)


def _font(size: int):
    from PIL import ImageFont

    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if Path(f).is_file():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def _thumb(item: dict):
    import cv2

    if not item.get("path"):
        return None
    if item["video"]:
        cap = cv2.VideoCapture(str(item["path"]))
        cap.set(cv2.CAP_PROP_POS_MSEC, (item["in"] + item["dur"] / 2) * 1000)
        ok, f = cap.read()
        cap.release()
    else:
        f, ok = cv2.imread(str(item["path"])), True
    if not ok or f is None:
        return None
    if item.get("crop"):
        x, y, w, h = item["crop"]
        f = f[y:y + h, x:x + w]
    return f


def storyboard(proj: Path, story: dict, items: list[dict], timing: dict) -> list[Path]:
    """qa/storyboard_<n>.jpg: each shot of story n next to the words said over it. Open them before the render: every
    picture must show its words (owner 2026-10-29: «предложение и иллюстрация должны быть связаны»)."""
    import cv2
    from PIL import Image, ImageDraw

    out, TW, TH, Wd = [], 360, 202, 1200
    for w, st in enumerate(stories(story)):
        rows = [it for it in items if it["w"] == w]
        img = Image.new("RGB", (Wd, 64 + (TH + 8) * len(rows)), (22, 22, 22))
        d = ImageDraw.Draw(img)
        d.text((16, 16), f"{w + 1}. {st.get('headline', '')}", font=_font(28), fill=(255, 214, 80))
        for k, it in enumerate(rows):
            y = 64 + k * (TH + 8)
            f = _thumb(it)
            if f is None:
                d.rectangle((16, y, 16 + TW, y + TH), outline=(220, 60, 60), width=3)
                d.text((30, y + TH // 2 - 14), "НЕТ КАРТИНКИ", font=_font(26), fill=(220, 60, 60))
            else:
                h, wd = f.shape[:2]
                sc = min(TW / wd, TH / h)
                f = cv2.cvtColor(cv2.resize(f, (int(wd * sc), int(h * sc))), cv2.COLOR_BGR2RGB)
                img.paste(Image.fromarray(f), (16 + (TW - f.shape[1]) // 2, y + (TH - f.shape[0]) // 2))
            said = " ".join(wd_[0] for wd_ in timing["words"] if it["start"] <= wd_[1] < it["start"] + it["dur"])
            lines, line = [], ""
            for word in said.split():
                if len(line) + len(word) > 40:
                    lines.append(line)
                    line = ""
                line = f"{line} {word}".strip()
            lines.append(line)
            d.text((400, y + 6), f"{k + 1}. {it['dur']:.1f} с", font=_font(22), fill=(150, 150, 150))
            d.multiline_text((400, y + 36), "\n".join(lines[:5]), font=_font(28), fill=(240, 240, 240), spacing=6)
            if it.get("thesis"):  # on screen in capitals over the bottom of the picture
                d.text((400, y + TH - 56), "ТЕЗИС: " + it["thesis"].replace("*", "").replace("|", " ").upper(),
                       font=_font(22), fill=(255, 90, 90))
            name = Path(it["path"]).name if it.get("path") else (it.get("src") or "?")
            d.text((400, y + TH - 26), name + (f" @{it['in']}" if it.get("video") else ""), font=_font(18),
                   fill=(120, 120, 120))
        p = proj / "qa" / f"storyboard_{w + 1}.jpg"
        p.parent.mkdir(exist_ok=True)
        img.save(p, quality=85)
        out.append(p)
    return out


def box_h(w: int, h: int) -> int:
    return int(min(BOX_MAX, max(BOX_MIN, W * h / w)))


def segment(item: dict, dst: Path) -> None:
    src, d = item["path"], item["dur"]
    w, h, _ = probe(src)
    crop = item.get("crop")  # [x, y, w, h] of the source: another framing of the same picture — a detail, a wide view
    if crop:
        w, h = crop[2], crop[3]
    full = h / w > 1.4  # portrait source: fill the whole frame
    bh = H if full else box_h(w, h)
    y = 0 if full else BOX_Y - bh // 2
    frames = max(1, round(d * FPS))
    bg = (f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=28:2,"
          f"eq=brightness=-0.12:saturation=0.8[bg]")
    if item["video"]:
        inp = ["-ss", str(item["in"]), "-t", f"{d:.3f}", "-i", str(src)]
        fg = f"[b]scale={W}:{bh}:force_original_aspect_ratio=increase,crop={W}:{bh}[fg]"
    else:  # still: slow push-in (rendered at 3x to keep zoompan from jittering)
        if item.get("label"):  # a Flow picture: paint out its corner sparkle first
            import cv2

            from addons.mural.build import remove_sparkle

            clean = dst.with_suffix(".src.png")
            cv2.imwrite(str(clean), remove_sparkle(cv2.imread(str(src))))
            src = clean
        inp = ["-loop", "1", "-t", f"{d:.3f}", "-i", str(src)]
        z0, z1 = item.get("zoom", (1.0, 1.0 + CARD_ZOOM))
        fg = (f"[b]scale={W * 3}:{bh * 3}:force_original_aspect_ratio=increase,crop={W * 3}:{bh * 3},"
              f"zoompan=z='{z0:g}+{z1 - z0:.4f}*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:"
              f"s={W}x{bh}:fps={FPS}[fg]")
    pre = f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else ""
    # a clip shorter than its cut (a slower voice than the one its pieces were cleaned for) holds its last frame
    hold = f"tpad=stop_mode=clone:stop_duration={d:.3f}," if item["video"] else ""
    flt = (f"[0:v]{pre}fps={FPS},{hold}split[a][b];{bg};{fg};[bg][fg]overlay=0:{y}:shortest=1,"
           f"trim=duration={d:.3f},setsar=1,format=yuv420p[v]")
    sh(["ffmpeg", "-v", "error", "-y", *inp, "-filter_complex", flt, "-map", "[v]", "-an", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", str(dst)])


def broll(proj: Path, items: list[dict]) -> list[Path]:
    """One silent clip per story window: assets/broll0.mp4, broll1.mp4, …"""
    d = proj / "assets" / "broll"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    outs = []
    for w in sorted({it["w"] for it in items}):
        lst = []
        for i, it in enumerate(items):
            if it["w"] == w:
                seg = d / f"s{i:02d}.mp4"
                segment(it, seg)
                lst.append(f"file '{seg.name}'")
        (d / f"list{w}.txt").write_text("\n".join(lst) + "\n")
        out = proj / "assets" / f"broll{w}.mp4"
        sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(d / f"list{w}.txt"), "-c", "copy",
            str(out)])
        outs.append(out)
    return outs


# -- sound bed --------------------------------------------------------------------------------------
SFX_DIRS = [Path("/opt/openmontage/sfx"), Path.home() / "Downloads" / "sfx_chp", REPO / "sfx"]
SOURCE_AUDIO = 0.0  # post footage is silent (owner 2026-10-28); a shot may still opt in with "audio": 0.35


def find_sfx(name: str) -> Path:
    p = Path(name).expanduser()
    if p.is_file():
        return p
    for d in SFX_DIRS:
        if (d / p.name).is_file():
            return d / p.name
    sys.exit(f"sfx {name} not found in {[str(d) for d in SFX_DIRS]}")


def has_audio(src: Path) -> bool:
    return bool(sh(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
                    str(src)]).strip())


def sound_bed(proj: Path, story: dict, timing: dict, items: list[dict]) -> Optional[Path]:
    """Every effect layer mixed into one track (assets/sfx.wav) that starts at 0 and lasts the whole film."""
    snd, total = story.get("sound") or {}, timing["duration"]
    layers = []  # (input args, filter chain, start time)

    def clip(src, start, dur, vol, offset=0.0, fade_in=0.05, fade_out=0.3, loop=False):
        if dur <= 0.05:
            return
        if loop:  # finite repeat count: an endless -stream_loop -1 input breaks amix's end of stream
            length = float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                               str(src)]).strip() or 0) or 1.0
            inp = ["-stream_loop", str(math.ceil((offset + dur) / length)), "-i", str(src)]
        else:
            inp = ["-i", str(src)]
        chain = (f"atrim=start={offset:.3f}:duration={dur:.3f},asetpts=PTS-STARTPTS,"
                 f"aformat=sample_rates=44100:channel_layouts=stereo,afade=t=in:d={fade_in},"
                 f"afade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out},volume={vol}")
        layers.append((inp, chain, start))

    if snd.get("sting"):
        x = snd["sting"]
        clip(find_sfx(x["src"]), float(x.get("at", 0.0)), float(x.get("dur", 3.5)), x.get("volume", 0.45), fade_out=0.8)
    swept = set()  # without the cat one story's window ends where the next begins: one whoosh there, not two
    for s, (b0, b1) in zip(stories(story), windows(timing)):
        x = (s.get("sound") or {}).get("ambience") or snd.get("ambience")  # a digest story may bring its own place
        if x and b1 > b0:
            clip(find_sfx(x["src"]), b0, b1 - b0, x.get("volume", 0.22), float(x.get("offset", 0)), 0.5, 0.6, loop=True)
        if snd.get("whoosh"):
            w = snd["whoosh"]
            for t in ([b0] if b0 > 0.5 else []) + ([b1] if b1 < total - 0.5 else []):
                if round(t, 1) not in swept:
                    swept.add(round(t, 1))
                    clip(find_sfx(w["src"]), max(0.0, t - 0.3), float(w.get("dur", 1.0)), w.get("volume", 0.35),
                         fade_out=0.4)
    for it in items:
        if it["video"] and it.get("audio", SOURCE_AUDIO) and has_audio(it["path"]):
            clip(it["path"], it["start"], it["dur"], it.get("audio", SOURCE_AUDIO), it["in"], 0.12, 0.12)
        if it.get("sfx"):
            x = it["sfx"]
            clip(find_sfx(x["src"]), it["start"] + float(x.get("at", 0)), float(x.get("dur", it["dur"])),
                 x.get("volume", 0.4), float(x.get("offset", 0)), 0.08, 0.4)
    if not layers:
        return None
    # input 0 is silence of exactly the film length; amix takes its length from it (duration=first), so the bed is
    # never shorter (a late first layer) nor endless (a looped ambience), and -t is a last hard stop
    args = ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
    parts = []
    for k, (inp, chain, start) in enumerate(layers, 1):
        args += inp
        ms = int(round(start * 1000))
        parts.append(f"[{k}:a]{chain},adelay={ms}|{ms}[l{k}]")
    mix = "[0:a]" + "".join(f"[l{k}]" for k in range(1, len(layers) + 1))
    parts.append(f"{mix}amix=inputs={len(layers) + 1}:normalize=0:duration=first:dropout_transition=0,"
                 f"alimiter=limit=0.9[out]")
    out = proj / "assets" / "sfx.wav"
    sh(["ffmpeg", "-v", "error", "-y", *args, "-filter_complex", ";".join(parts), "-map", "[out]", "-t", f"{total:.3f}",
        "-ar", "44100", str(out)])
    print(f"sound bed: {len(layers)} layers -> {out.name}", flush=True)
    return out


# -- composition -------------------------------------------------------------------------------------
def anchor_dir(proj: Path, d: str) -> Path:
    """An anchor dir is relative to the project, else to the repo (the shared cat: addons/news/anchor_cat)."""
    p = Path(d)
    if p.is_absolute():
        return p
    return (proj / p).resolve() if (proj / p).is_dir() else (REPO / p).resolve()


def greeting_clip(proj: Path, story: dict, timing: dict, ws: Path) -> Optional[tuple[float, float]]:
    """A generated video of the anchor saying the greeting (anchor.greeting_clip: {"src", "speech": [t0, t1] in the
    clip, "words": n script words it says}) is re-timed so its line lands on the narration's first n words, muted,
    and laid over the lip-flap anchor; returns (start, duration) in composition time."""
    g = (story.get("anchor") or {}).get("greeting_clip")
    if not g:
        return None
    src = (proj / g["src"]) if (proj / g["src"]).is_file() else (REPO / g["src"])
    v0, v1 = g["speech"]
    words = timing["words"][:int(g["words"])]
    g0, g1 = words[0][1], words[-1][2]
    k = (g1 - g0) / (v1 - v0)  # >1 slows the clip down
    if not 0.7 <= k <= 1.4:
        print(f"warning: greeting clip re-timed by {k:.2f} — lip sync will look off", flush=True)
    _, _, length = probe(src)
    t_start = g0 - v0 * k
    in_off = max(0.0, -t_start / k)
    in_end = min(length, v1 + float(g.get("tail", 0.6)))
    start = max(0.0, t_start)
    dur = min((in_end - in_off) * k, windows(timing)[0][0] - start)
    sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{in_off:.3f}", "-t", f"{dur / k:.3f}", "-i", str(src), "-an", "-vf",
        f"setpts=(PTS-STARTPTS)*{k:.4f},fps={FPS},scale={W}:{H}:flags=lanczos,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", str(ws / "assets" / "greet.mp4")])
    print(f"greeting clip: x{k:.2f}, {start:.2f}s + {dur:.2f}s", flush=True)
    return round(start, 3), round(dur, 3)


TV_BOX = (486, 102, 568, 326)  # the right studio monitor in the anchor frame (x, y, w, h), anchor_cat


def tv_schedule(story: dict, timing: dict) -> list[tuple[int, float, float]]:
    """When the studio monitor shows story i: while the anchor leads into it and while he comments on it."""
    if not story.get("stories"):
        return []
    words, out, k = timing["words"], [], 0
    n_intro = len(plain((story.get("anchor") or {}).get("intro", "")).split())
    W_ = windows(timing)
    for p in parts(story):
        n = len(plain(p["text"]).split())
        if p["kind"] == "anchor" and p["i"] is not None and n:
            first = k + (n_intro if k == 0 else 0)  # the greeting itself keeps the monitor's own picture
            b0, b1 = W_[p["i"]]
            if first < k + n and words[first][1] < b0:  # the lead before story i
                out.append((p["i"], round(words[first][1] - 0.1, 3), round(b0 + 0.2, 3)))
            elif words[k][1] > b1:  # the comment after story i
                out.append((p["i"], round(b1 - 0.2, 3), round(words[k + n - 1][2] + 0.25, 3)))
        k += n
    return out


def tv_stills(proj: Path, story: dict, ws: Path) -> list[Optional[str]]:
    """One 16:9 still per story for the studio monitor: its first real photo/video frame, else its first picture."""
    out = []
    for i, s in enumerate(stories(story)):
        shots = s.get("shots") or []
        pick = next((x for x in shots if not x.get("label")), shots[0] if shots else None)
        if not pick or not s.get("tv"):
            out.append(None)
            continue
        src = (proj / pick["src"]).resolve()
        dst = ws / "assets" / f"tv{i}.jpg"
        if pick.get("label"):  # a Flow picture: paint out its corner sparkle before cropping
            import cv2

            from addons.mural.build import remove_sparkle

            clean = ws / "assets" / f"tv{i}.src.png"
            cv2.imwrite(str(clean), remove_sparkle(cv2.imread(str(src))))
            src = clean
        w, h = TV_BOX[2] * 2, TV_BOX[3] * 2
        inp = ["-ss", f"{float(pick.get('in', 0.3)) + 1.0:.2f}", "-i", str(src)] if src.suffix.lower() in VIDEO_EXT \
            else ["-i", str(src)]
        sh(["ffmpeg", "-v", "error", "-y", *inp, "-frames:v", "1", "-vf",
            f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}", "-q:v", "3", str(dst)])
        out.append(dst.name)
    return out


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# -- theses: the fact of a phrase in large capitals over the bottom of its picture (owner 2026-10-30) ---------------
THESIS_FONT = HERE / "fonts" / "PTSansNarrow-Bold.ttf"  # ParaType Free Font License (in the font's own name table)
THESIS_MAX = 44  # characters: a headline over the picture, not a sentence
THESIS_MIN_S, THESIS_MAX_S = 4.0, 7.0  # a thesis stays at least two phrase-long cuts (owner 2026-10-30: «хотя бы на
# 2-х кадрах»), over the next cuts of its story if its own is short, and leaves a long clip after 7 s; "span": N cuts
THESIS_CSS = """
    @font-face { font-family: "PT Sans Narrow"; src: url("assets/PTSansNarrow-Bold.ttf"); font-weight: 700; }
    .thesis { position: absolute; left: 0; right: 0; z-index: 5; padding: 240px 56px 44px;
              background: linear-gradient(to bottom, rgba(0,0,0,0), rgba(0,0,0,.46) 45%, rgba(0,0,0,.74)); }
    .thesis .t { font-family: "PT Sans Narrow", sans-serif; font-weight: 700; font-size: 92px;
                 line-height: 1.04; text-transform: uppercase; letter-spacing: .5px; color: #fff;
                 text-shadow: 0 3px 16px rgba(0,0,0,.7), 0 1px 3px rgba(0,0,0,.85); }
    .thesis em { font-style: normal; padding: 0 12px; border-radius: 6px; text-shadow: none;
                 background: linear-gradient(#e3001b, #e3001b) no-repeat 0 0 / 100% 100%;
                 -webkit-box-decoration-break: clone; box-decoration-break: clone; }"""


def thesis_markup(text: str) -> str:
    """«Задержаны *трое* иностранцев» → the starred words on our red marker; «|» breaks the line by hand."""
    return "<br>".join(re.sub(r"\*([^*]+)\*", r"<em>\1</em>", esc(part.strip())) for part in text.split("|"))


def thesis_bottom(it: dict) -> int:
    """Where the picture of a cut ends on screen (segment's layout): the thesis sits on that edge."""
    w, h, _ = probe(it["path"])
    if it.get("crop"):
        w, h = it["crop"][2], it["crop"][3]
    return 1480 if h / w > 1.4 else BOX_Y + box_h(w, h) // 2  # full frame: just above the captions (top 1500)


def theses(items: list[dict]) -> tuple[list[str], list[str], list[tuple[float, float]]]:
    """HTML, timeline and the spans they hold the screen for. A thesis starts on its cut and stays over the next ones
    of its story ("span", 2 by default) in one place — on the edge of its first picture, so it does not jump at the
    cut; the running captions give way to it (it says the same phrases shorter) and come back after it."""
    html, js, spans = [], [], []
    marked = [(n, i) for n, i in enumerate(items) if i.get("thesis") and i.get("path")]
    for k, (n, it) in enumerate(marked):
        story = [x for x in items[n:] if x.get("w") == it.get("w")]
        a = it["start"]
        if it.get("span"):
            run = story[:int(it["span"])]
            d = round(run[-1]["start"] + run[-1]["dur"] - a, 3)
        else:  # by time: whole cuts up to 4 s at least, never past 7 s
            end = next((x["start"] + x["dur"] for x in story if x["start"] + x["dur"] - a >= THESIS_MIN_S),
                       story[-1]["start"] + story[-1]["dur"])
            d = round(min(end - a, THESIS_MAX_S), 3)
        html.append(f'    <div id="th{k}" class="clip thesis" data-start="{a}" data-duration="{d}" data-track-index="4" '
                    f'style="bottom:{H - thesis_bottom(it)}px"><div class="t">{thesis_markup(it["thesis"])}</div></div>')
        if a < 0.05:  # on the very first frame (a short's cover, its hook): there at once, no entrance
            spans.append((a, round(a + d, 3)))
            continue
        js.append(f'  tl.from("#th{k}", {{opacity: 0, duration: 0.25, ease: "none"}}, {a:.2f});\n'
                  f'  tl.from("#th{k} .t", {{y: 46, opacity: 0, duration: 0.4, ease: "power3.out"}}, {a + 0.1:.2f});\n'
                  f'  tl.from("#th{k} em", {{backgroundSize: "0% 100%", duration: 0.35, ease: "power2.out", '
                  f'stagger: 0.1}}, {a + 0.35:.2f});')
        spans.append((a, round(a + d, 3)))
    return html, js, spans


def around(caps: list[tuple[float, float, str]], spans: list[tuple[float, float]]) -> list[tuple[float, float, str]]:
    """Captions outside the theses: one that runs into a thesis is cut where it starts, one under it is dropped."""
    out = []
    for a, b, t in caps:
        if any(s <= a < e for s, e in spans):
            continue
        b = min([b, *(s for s, _ in spans if a < s < b)])
        if b - a >= 0.05:
            out.append((a, b, t))
    return out


def thesis_warnings(story: dict) -> list[str]:
    out = []
    for w, st in enumerate(stories(story)):
        shots = st.get("shots", [])
        ts = [s["thesis"] for s in shots if s.get("thesis")]
        if shots and not shots[0].get("thesis"):
            out.append(f"story {w + 1}: no thesis on its first shot — every story opens with one (owner 2026-10-30)")
        on = [j for j, s in enumerate(shots) if s.get("thesis")]
        out += [f"story {w + 1}: the thesis of shot {j + 1} comes right after the one of shot {i + 1} — let the "
                f"first one stay" for i, j in zip(on, on[1:]) if j == i + 1]
        out += [f"story {w + 1}: thesis «{t}» is {len(t.replace('*', '').replace('|', ''))} characters — at most "
                f"{THESIS_MAX}" for t in ts if len(t.replace("*", "").replace("|", "")) > THESIS_MAX]
        if len(ts) > 2:
            out.append(f"story {w + 1}: {len(ts)} theses — one or two per story, on its strongest facts")
    return out


def write_workspace(proj: Path, story: dict, timing: dict, items: list[dict]) -> Path:
    ws = proj / "hyperframes"
    shutil.rmtree(ws / "assets", ignore_errors=True)
    (ws / "assets").mkdir(parents=True)
    shutil.copy2(REPO / "addons" / "mural" / "vendor" / "gsap.min.js", ws / "assets" / "gsap.min.js")
    W_ = windows(timing)
    for w in range(len(W_)):
        shutil.copy2(proj / "assets" / f"broll{w}.mp4", ws / "assets" / f"broll{w}.mp4")
    shutil.copy2(proj / "assets" / "narration.mp3", ws / "assets" / "narration.mp3")
    duration = timing["duration"]
    music = dict(story.get("music") or {})
    found = find_music(music["src"]) if music.get("src") else None
    if music.get("src") and found is None:
        print(f"music {music['src']} not found — rendering without music", flush=True)
    if found:
        shutil.copy2(found, ws / "assets" / "music.mp3")
    b0 = W_[0][0]
    anchor_html, anchor_js = "", ""
    a = story.get("anchor") or {}
    if a.get("dir"):
        L = anchor_dir(proj, a["dir"]) / "layers"
        layers = json.loads((L / "layers.json").read_text())
        (ws / "assets" / "anchor").mkdir()
        for f in [f"{k}.png" for k in layers]:
            shutil.copy2(L / f, ws / "assets" / "anchor" / f)
        # the same cat in a fresh scene by the day — rain, snow, the city at night (owner 2026-10-30, anchor_looks.py)
        from datetime import date

        from addons.news.anchor_looks import pick

        shutil.copy2(pick(L.parent, story, date.today().toordinal()), ws / "assets" / "anchor" / "base.jpg")
        imgs = "".join(f'<img id="a_{k}" class="patch" src="assets/anchor/{k}.png" alt="" '
                       f'style="left:{x}px;top:{y}px;width:{w}px;height:{h}px" />' for k, (x, y, w, h) in layers.items())
        # the studio monitor behind the anchor shows the story he is leading into / commenting on
        tv_js, tv_html = [], ""
        stills = tv_stills(proj, story, ws)
        if any(stills):
            x, y, w, h = TV_BOX
            screens = "".join(
                f'<div id="tv{i}" class="tvs"><img src="assets/{f}" alt="" />'
                f'<div class="tvbar">{esc(stories(story)[i]["tv"])}</div></div>' for i, f in enumerate(stills) if f)
            tv_html = f'<div id="tv" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px">{screens}</div>'
            for i, t_on, t_off in tv_schedule(story, timing):
                if stills[i]:
                    tv_js.append(f'  tl.to("#tv{i}", {{opacity: 1, duration: 0.2, ease: "none"}}, {t_on:.2f});')
                    tv_js.append(f'  tl.to("#tv{i}", {{opacity: 0, duration: 0.2, ease: "none"}}, {t_off:.2f});')
        anchor_html = (f'    <div id="anchor" class="clip" data-start="0" data-duration="{duration}" data-track-index="1">'
                       f'<div id="anchor-cam"><img id="a_base" src="assets/anchor/base.jpg" alt="" />{imgs}{tv_html}'
                       f'</div></div>')
        # the anchor is on screen in the gaps around the stories' b-roll: intro, the leads between stories, outro
        edges = [0.0] + [t for w in W_ for t in w] + [duration]
        on_air = [(edges[k], edges[k + 1]) for k in range(0, len(edges), 2) if edges[k + 1] - edges[k] > 0.2]
        js = []
        for k, (w0, w1) in enumerate(on_air):  # slow push-in while the anchor speaks
            js.append(f'  tl.fromTo("#anchor-cam", {{scale: 1.0}}, {{scale: 1.045, duration: {w1 - w0:.2f}, ease: "none", '
                      f'immediateRender: {str(k == 0).lower()}}}, {w0:.2f});')
        greet = greeting_clip(proj, story, timing, ws)
        if greet:  # the generated greeting sits over the lip-flap anchor with the same push-in, then dissolves into it
            g0, gd = greet
            w0, w1 = on_air[0]
            anchor_html += (f'\n    <video id="greet" class="clip" src="assets/greet.mp4" data-start="{g0}" '
                            f'data-duration="{gd}" data-track-index="6" muted playsinline></video>')
            js.append(f'  tl.fromTo("#greet", {{scale: 1.0}}, {{scale: 1.045, duration: {w1 - w0:.2f}, ease: "none"}}, {w0:.2f});')
            js.append(f'  tl.to("#greet", {{opacity: 0, duration: 0.3, ease: "none"}}, {g0 + gd - 0.3:.2f});')
        if "open" in layers:
            for t, st in anc.mouth_track(proj / "assets" / "narration.mp3", FPS, on_air, VOICE_AT, "mid" in layers):
                js.append(f'  tl.set("#a_open", {{opacity: {int(st == "open")}}}, {t});')
                if "mid" in layers:
                    js.append(f'  tl.set("#a_mid", {{opacity: {int(st == "mid")}}}, {t});')
        if "blink" in layers:
            for t, d in anc.blink_track(on_air):
                js.append(f'  tl.set("#a_blink", {{opacity: 1}}, {t}); tl.set("#a_blink", {{opacity: 0}}, {t + d:.2f});')
        anchor_js = "\n".join(js + tv_js)
    thesis_html, thesis_js, spans = theses(items)
    if thesis_html:
        shutil.copy2(THESIS_FONT, ws / "assets" / THESIS_FONT.name)
    caps = around(word_cues(timing["words"]), spans)
    caps_html = "\n".join(
        f'    <div id="cap{i}" class="clip cap" data-start="{a}" data-duration="{b - a:.2f}" data-track-index="5">'
        f'<span>{esc(t)}</span></div>' for i, (a, b, t) in enumerate(caps))
    caps_js = "\n".join(
        f'  tl.from("#cap{i} span", {{opacity: 0, scale: 0.92, duration: 0.14, ease: "power2.out"}}, {a});'
        for i, (a, _, _) in enumerate(caps))
    chips, k = [], 0
    runs: list[dict] = []  # consecutive cuts with the same credit and box share one chip
    for it in items:
        if runs and runs[-1].get("credit") == it.get("credit") and runs[-1]["path"] == it["path"] \
                and runs[-1].get("label") == it.get("label"):
            runs[-1] = {**runs[-1], "dur": round(runs[-1]["dur"] + it["dur"], 3)}
        else:
            runs.append(dict(it))
    for it in runs:  # label / credit chips ride with their shot
        w, h, _ = probe(it["path"])
        full = h / w > 1.4
        bh = H if full else box_h(w, h)
        top = 430 if full else BOX_Y - bh // 2
        bottom = H - 1480 if full else H - (BOX_Y + bh // 2)  # full frame: sit just above the captions (top 1500)
        # no on-screen AI mark (owner 2026-10-28: a faint overlay reads as a foreign watermark and cuts reach; the owner
        # ticks the platform's AI-content box at upload). "label" still marks Flow pictures for the build itself
        if it.get("label") and story.get("show_ai_label"):
            chips.append(f'    <div id="chip{k}" class="clip chip label" data-layout-ignore style="bottom:{bottom + 14}px" data-start="{it["start"]}" '
                         f'data-duration="{it["dur"]}" data-track-index="4">{esc(it["label"])}</div>')
            k += 1
        # source credits stay in story.json and the post text, not on screen (owner 2026-10-28); a story can opt back in
        if it.get("credit") and story.get("show_credits"):
            chips.append(f'    <div id="chip{k}" class="clip chip credit" style="bottom:{bottom + 18}px" '
                         f'data-start="{it["start"]}" data-duration="{it["dur"]}" data-track-index="4">'
                         f'{esc(it["credit"])}</div>')
            k += 1
    music_vol = music.get("volume", 0.12)
    audio = (f'    <audio id="bgm" src="assets/music.mp3" data-start="0" data-duration="{duration}" '
             f'data-media-start="{music.get("offset", 0)}" data-track-index="10" data-volume="{music_vol}"></audio>\n'
             if found else "")
    audio += '    <audio id="voice" src="assets/narration.mp3" data-start="0.3" data-track-index="11" data-volume="1"></audio>'
    if (proj / "assets" / "sfx.wav").is_file():
        shutil.copy2(proj / "assets" / "sfx.wav", ws / "assets" / "sfx.wav")
        audio += (f'\n    <audio id="sfx" src="assets/sfx.wav" data-start="0" data-duration="{duration}" '
                  f'data-track-index="12" data-volume="1"></audio>')
    audio_js = f'  tl.to("#bgm", {{ volume: 0, duration: 1.5, ease: "power1.in" }}, {duration - 1.5:.2f});' \
        if found and not story.get("loop") else ""
    head = esc(story.get("headline") or stories(story)[0]["headline"])
    S_ = stories(story)
    broll_html, plate_html, plate_js = [], [], []
    intro = timing.get("intro")  # narrator mode: the greeting runs over the teaser under the film's own plate
    if intro:
        plate_html.append(
            f'    <div id="plateI" class="clip plate" data-start="0" data-duration="{intro[1]:.3f}" data-track-index="3">\n'
            f'      <span class="tag">{esc(story.get("tag", "ЧП · МОСКВА"))}</span>'
            f'<span class="date">{esc(story.get("date", ""))}</span>\n      <div class="head">{head}</div>\n    </div>')
        plate_js.append('  tl.from("#plateI .tag", { x: -60, opacity: 0, duration: 0.35, ease: "power3.out" }, 0.3);\n'
                        '  tl.from("#plateI .head", { y: -24, opacity: 0, duration: 0.4, ease: "power3.out" }, 0.45);')
    for w, (s, (w0, w1)) in enumerate(zip(S_, W_)):
        date = esc(s.get("date") or story.get("date", ""))
        if len(S_) > 1:
            date = f"{w + 1} из {len(S_)} · {date}" if date else f"{w + 1} из {len(S_)}"
        broll_html.append(f'    <video id="broll{w}" class="clip broll" src="assets/broll{w}.mp4" data-start="{w0}" '
                          f'data-duration="{w1 - w0:.3f}" data-track-index="0" muted playsinline></video>')
        p0 = intro[1] if w == 0 and intro else w0  # narrator mode: the first story's plate comes with its lead
        plate_html.append(
            f'    <div id="plate{w}" class="clip plate" data-start="{p0}" data-duration="{w1 - p0:.3f}" data-track-index="3">\n'
            f'      <span class="tag">{esc(story.get("tag", "ЧП · МОСКВА"))}</span><span class="date">{date}</span>\n'
            f'      <div class="head">{esc(s["headline"]) if len(S_) > 1 else head}</div>\n    </div>')
        if p0 >= 0.05:  # a plate on the first frame (a cold open, a short) is the cover: there at once
            plate_js.append(
                f'  tl.from("#plate{w} .tag", {{ x: -60, opacity: 0, duration: 0.35, ease: "power3.out" }}, {p0 + 0.05:.2f});\n'
                f'  tl.from("#plate{w} .date", {{ opacity: 0, duration: 0.3 }}, {p0 + 0.25:.2f});\n'
                f'  tl.from("#plate{w} .head", {{ y: -24, opacity: 0, duration: 0.4, ease: "power3.out" }}, {p0 + 0.15:.2f});')
    lower_html = lower_js = ""
    if b0 > 0.5:  # anchor intro: the headline sits as a lower third over the desk
        lower_html = (f'    <div id="lower" class="clip" data-start="0" data-duration="{b0}" data-track-index="2">'
                      f'<span class="tag">{esc(story.get("tag", "ЧП · МОСКВА"))}</span><div class="head">{head}</div></div>')
        lower_js = ('  tl.from("#lower .tag", { x: -60, opacity: 0, duration: 0.35, ease: "power3.out" }, 0.3);\n'
                    '  tl.from("#lower .head", { x: -80, opacity: 0, duration: 0.45, ease: "power3.out" }, 0.45);')
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width={W}, height={H}" />
  <title>{esc(story.get('title', story['slug']))}</title>
  <script src="assets/gsap.min.js"></script>
  <style>
    body {{ margin: 0; background: #0b0b0d; }}
    #root {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; background: #0b0b0d;
             font-family: "Arial Black", "DejaVu Sans", Arial, sans-serif; }}
    .broll {{ position: absolute; left: 0; top: 0; width: {W}px; height: {H}px; object-fit: cover; z-index: 2; }}
    #anchor {{ position: absolute; inset: 0; overflow: hidden; z-index: 1; }}
    #anchor-cam {{ position: absolute; left: 0; top: 0; width: {W}px; height: {H}px; transform-origin: 50% 32%; }}
    #anchor-cam img {{ position: absolute; display: block; }}
    #a_base {{ left: 0; top: 0; width: {W}px; height: {H}px; }}
    #anchor-cam .patch {{ opacity: 0; }}
    #tv {{ position: absolute; overflow: hidden; }}
    #tv .tvs {{ position: absolute; inset: 0; opacity: 0; }}
    #tv .tvs img {{ position: absolute; left: 0; top: 0; width: 100%; height: 100%; object-fit: cover; }}
    #tv .tvbar {{ position: absolute; left: 0; right: 0; bottom: 0; background: #e3001b; color: #fff; font-size: 30px;
                 font-weight: 900; letter-spacing: 1px; padding: 8px 16px 6px; white-space: nowrap; overflow: hidden; }}
    #greet {{ position: absolute; left: 0; top: 0; width: {W}px; height: {H}px; object-fit: cover; z-index: 2;
              transform-origin: 50% 32%; }}
    #lower {{ position: absolute; left: 40px; right: 40px; top: 1180px; z-index: 6; }}
    #lower .tag {{ display: inline-block; background: #e3001b; color: #fff; font-size: 38px; font-weight: 900;
                   letter-spacing: 2px; padding: 9px 20px 7px; border-radius: 6px; }}
    #lower .head {{ margin-top: 12px; background: rgba(10,10,12,.88); color: #fff; font-size: 50px; line-height: 1.12;
                    font-weight: 900; padding: 18px 24px; border-left: 10px solid #e3001b; border-radius: 4px; }}
    .plate {{ position: absolute; left: 48px; right: 48px; top: 120px; z-index: 6; }}
    .plate .tag {{ display: inline-block; background: #e3001b; color: #fff; font-size: 40px; font-weight: 900;
                   letter-spacing: 2px; padding: 10px 22px 8px; border-radius: 6px; }}
    .plate .date {{ display: inline-block; margin-left: 14px; color: #fff; font-family: Arial, "DejaVu Sans", sans-serif;
                    font-weight: 700; font-size: 34px; background: rgba(10,10,12,.86); padding: 9px 16px 8px;
                    border-radius: 6px; }}
    .plate .head {{ margin-top: 18px; background: rgba(10,10,12,.86); color: #fff; font-size: 54px; line-height: 1.12;
                    font-weight: 900; padding: 20px 26px; border-left: 10px solid #e3001b; border-radius: 4px; }}
    .chip {{ position: absolute; z-index: 6; font-family: Arial, "DejaVu Sans", sans-serif; font-weight: 700; color: #fff; }}
    .chip.label {{ right: 22px; font-size: 20px; font-weight: 400; color: rgba(255,255,255,.42);
                   text-shadow: 0 1px 2px rgba(0,0,0,.35); }}
    .chip.credit {{ left: 30px; font-size: 30px; background: rgba(0,0,0,.55); padding: 5px 12px; border-radius: 4px; }}
    .cap {{ position: absolute; left: 50px; right: 50px; top: 1500px; text-align: center; z-index: 7; }}
    .cap span {{ display: inline-block; font-weight: 900; font-size: 66px; line-height: 1.15; color: #fff;
                 background: rgba(0,0,0,.55); padding: 8px 18px 10px; border-radius: 10px;
                 text-shadow: 0 2px 6px rgba(0,0,0,.6); }}
    #fade {{ position: absolute; inset: 0; background: #000; opacity: 0; pointer-events: none; z-index: 9; }}{THESIS_CSS if thesis_html else ""}
  </style>
</head>
<body>
  <div id="root" data-composition-id="main" data-width="{W}" data-height="{H}" data-duration="{duration}">
{chr(10).join(broll_html)}
{anchor_html}
{lower_html}
{chr(10).join(plate_html)}
{chr(10).join(chips)}
{chr(10).join(thesis_html)}
{caps_html}
    <div id="fade" class="clip" data-start="0" data-duration="{duration}" data-track-index="8"></div>
{audio}
  </div>
<script>
  window.__timelines = window.__timelines || {{}};
  const tl = gsap.timeline({{ paused: true }});
{chr(10).join(plate_js)}
{lower_js}
{anchor_js}
{chr(10).join(thesis_js)}
{caps_js}
{audio_js}
{"" if story.get("loop") else f'  tl.to("#fade", {{ opacity: 1, duration: 0.6, ease: "power1.in" }}, {duration - 0.6:.2f});'}
  window.__timelines["main"] = tl;
</script>
</body>
</html>
"""
    (ws / "index.html").write_text(html, encoding="utf-8")
    (ws / "hyperframes.json").write_text(json.dumps({"name": story["slug"], "fps": FPS}, indent=1))
    (ws / "DESIGN.md").write_text(f"# {story.get('title', story['slug'])}\n\nLocal-news short: {len(items)} b-roll cuts "
                                  f"of {SHOT_S:.0f}s under a headline plate, word-timed captions, voice + music. "
                                  "Built by addons/news/build.py.\n")
    return ws


def qa(proj: Path, video: Path, items: list[dict]) -> dict:
    (proj / "qa").mkdir(exist_ok=True)
    tiles = proj / "qa" / "tiles"
    shutil.rmtree(tiles, ignore_errors=True)
    tiles.mkdir()
    for i, it in enumerate(items):  # one frame from the middle of every cut
        sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{it['start'] + it['dur'] / 2:.2f}", "-i", str(video),
            "-frames:v", "1", "-vf", "scale=270:480", str(tiles / f"{i:02d}.jpg")])
    cols = min(len(items), 6)
    sh(["ffmpeg", "-v", "error", "-y", "-pattern_type", "glob", "-i", str(tiles / "*.jpg"), "-vf",
        f"tile={cols}x{math.ceil(len(items) / cols)}", "-frames:v", "1", str(proj / "qa" / "contact.jpg")])
    info = json.loads(sh(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "json", str(video)]))
    report = {"probe": info, "cuts": len(items), "contact": str(proj / "qa" / "contact.jpg")}
    (proj / "qa" / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return report


def part_words(story: dict, words: list) -> list[dict]:
    """Each read part with its timed words: {"role", "i", "words"}."""
    out, k = [], 0
    for p in parts(story):
        n = len(plain(p["text"]).split())
        out.append({**p, "words": words[k:k + n]})
        k += n
    return out


def split_story(proj: Path, story: dict, timing: dict, items: list[dict], i: int) -> Path:
    """One story of a digest as its own short for TikTok (owner 2026-10-30, the review: one story of 15–25 s, no
    greeting, the hardest picture and a big headline in the first frame, a loop): its pictures from the first second
    under its plate (the headline) and its opening thesis, the voice from the story's first word — the lead and the
    greeting are left out — to the end of the cat's comment, no fade-out, so the end runs back into the start. Made
    from the digest's own voice and pictures: projects/<id>/tiktok/<n>/, rendered to renders/tiktok_<n>.mp4."""
    pw = part_words(story, timing["words"])
    text = next(p for p in pw if p["role"] == "story" and p["i"] == i)["words"]
    comment = next((p for p in pw if p["role"] == "comment" and p["i"] == i), None)
    tail = (comment or {}).get("words") or text
    s0 = max(VOICE_AT, text[0][1] - 0.25)
    after = [w for w in timing["words"] if w[1] > tail[-1][1]]  # the next lead must not be heard
    end = min(tail[-1][2] + 0.6, after[0][1] - 0.05) if after else tail[-1][2] + 0.6
    shift = s0 - VOICE_AT  # composition time t of the digest is t - shift in the short (the voice still starts at 0.3)
    sub = proj / "tiktok" / f"{i + 1}"
    shutil.rmtree(sub, ignore_errors=True)
    (sub / "assets").mkdir(parents=True)
    sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{s0 - VOICE_AT:.3f}", "-t", f"{end - s0:.3f}", "-i",
        str(proj / "assets" / "narration.mp3"), "-c:a", "libmp3lame", "-q:a", "2", str(sub / "assets" / "narration.mp3")])
    s = stories(story)[i]
    w0, w1 = windows(timing)[i]
    duration = round(end - shift, 2)
    t = {"words": [[w, round(a - shift, 3), round(b - shift, 3)] for w, a, b in timing["words"]
                   if s0 - 0.05 <= a <= tail[-1][1]],
         "duration": duration, "speech_end": round(tail[-1][2] - shift, 3),
         "windows": [[0.0, round(min(w1, end) - shift, 3)]]}
    t["broll"] = t["windows"][0]
    its = [{**it, "start": round(it["start"] - shift, 3), "w": 0} for it in items if it["w"] == i]
    its = [it for it in its if it["start"] < t["windows"][0][1]]
    its[0] = {**its[0], "dur": round(its[0]["dur"] + its[0]["start"], 3), "start": 0.0}  # a picture from the first frame
    last = its[-1]
    its[-1] = {**last, "dur": round(t["windows"][0][1] - last["start"], 3)}
    a = story.get("anchor") or {}
    one = {**story, "stories": [s], "headline": s.get("headline", story.get("headline")), "loop": True,
           "anchor": {**a, "intro": "", "outro": ""}, "date": s.get("date", story.get("date"))}
    one.pop("cold_open", None)
    broll(sub, its)
    sound_bed(sub, one, t, its)
    ws = write_workspace(sub, one, t, its)
    out = proj / "renders" / f"tiktok_{i + 1}.mp4"
    render(ws, out)
    return out


def unclean_footage(story: dict) -> list[str]:
    """Post media that has not been through addons/news/clean_footage.py (owner 2026-10-28: no foreign watermarks).
    Commons photos (credit names Wikimedia) and illustrations are not post media; "footage_clean": false opts an old
    project out."""
    if story.get("footage_clean") is False:
        return []
    bad = []
    for s in stories(story):
        for sh_ in s.get("shots", []):
            src = sh_.get("src", "")
            if (src.startswith("media/") and not src.startswith("media/clean/") and sh_.get("clean") != "none"
                    and "Wikimedia" not in (sh_.get("credit") or "")):
                bad.append(src)
    return sorted(set(bad))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--voice-only", action="store_true", help="voice + timings, then print how many shots are needed")
    ap.add_argument("--retime", action="store_true", help="reuse assets/narration.mp3")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--storyboard", action="store_true",
                    help="only qa/storyboard_<n>.jpg (each picture next to its words) and the picture warnings")
    ap.add_argument("--split", nargs="?", const="all", metavar="N,N",
                    help="also one short per story for TikTok (renders/tiktok_<n>.mp4); with --retime and the digest "
                         "already built it only cuts and renders the shorts")
    a = ap.parse_args()
    proj = a.project.resolve()
    story = json.loads((proj / "story.json").read_text(encoding="utf-8"))
    from addons.news import visuals  # maps of the place: real pictures from data, not AI (owner 2026-10-29)

    if made := visuals.resolve(proj, story):
        print(f"{made} map shots made from data: assets/visuals/", flush=True)
    timing = voice(proj, story, a.retime or a.storyboard)
    phrases = story_beats(story, timing)
    (proj / "assets" / "beats.json").write_text(json.dumps(phrases, ensure_ascii=False, indent=1), encoding="utf-8")
    need = [(len(b), len(s.get("shots", []))) for s, b in zip(stories(story), phrases)]
    print(f"speech {timing['speech_end']:.1f}s, video {timing['duration']:.1f}s, shots needed "
          + ", ".join(f"{n} (have {h})" for n, h in need), flush=True)
    for i, b in enumerate(phrases):  # a shot per phrase, in this order: what it shows goes with what is said
        print(f"story {i + 1} phrases:" + "".join(f"\n  {k + 1}. {p['end'] - p['start']:.1f}s  {p['text']}"
                                               for k, p in enumerate(b)), flush=True)
    if a.voice_only:
        return
    ai, total, limit = ai_budget(story)
    over = total and ai / total > limit + 1e-9
    print(f"AI pictures: {ai} of {total} shots, allowed {limit:.0%} (cities.json ai_max_share)"
          + (" — OVER: use the post's footage at other in-points, crops of its photos, Commons photos of the very "
             "place, or drop the story" if over else ""), flush=True)
    for warning in picture_warnings(story) + thesis_warnings(story) + unused_post_media(proj, story):
        print("warning:", warning, flush=True)
    if a.storyboard:
        items = film_items(proj, story, timing, phrases, strict=False)
        print("storyboard:", " ".join(str(p.relative_to(proj)) for p in storyboard(proj, story, items, timing)))
        return
    if over:
        sys.exit(f"{ai} of {total} shots are AI pictures, the limit is {limit:.0%} (owner 2026-10-29: as few as possible)")
    if bad := unclean_footage(story):
        sys.exit("post footage not cleaned of watermarks — run addons/news/clean_footage.py first: " + ", ".join(bad))
    items = film_items(proj, story, timing, phrases)
    if a.split and a.retime and (proj / "renders" / "final.mp4").is_file():  # the digest is done: only the shorts
        for i in (range(len(stories(story))) if a.split == "all" else [int(x) - 1 for x in a.split.split(",")]):
            print("short:", split_story(proj, story, timing, items, i), flush=True)
        return
    storyboard(proj, story, items, timing)
    broll(proj, items)
    (proj / "assets" / "sfx.wav").unlink(missing_ok=True)
    sound_bed(proj, story, timing, items)
    ws = write_workspace(proj, story, timing, items)
    print("workspace", ws, flush=True)
    if a.no_render:
        return
    out = proj / "renders" / "final.mp4"
    render(ws, out)
    print(json.dumps(qa(proj, out, items), ensure_ascii=False)[:400])
    if a.split and story.get("stories"):
        for i in (range(len(stories(story))) if a.split == "all" else [int(x) - 1 for x in a.split.split(",")]):
            print("short:", split_story(proj, story, timing, items, i), flush=True)


if __name__ == "__main__":
    main()
