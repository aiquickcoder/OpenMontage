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
  sound (video shots with sound keep it at 0.35 by default; "audio": 0 mutes it). SFX files live in SFX_DIRS.

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
TAIL_S = 1.2           # hold after the last word
VOICE_AT = 0.3         # the voice starts this far into the composition; every timing below is composition time
BOX_Y = 930            # centre line of the picture box (the plate sits above it, captions below)
BOX_MIN, BOX_MAX = 760, 1000
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}
PIPER_MODELS = Path(os.environ.get("PIPER_MODELS", Path.home() / ".piper" / "models"))
TAG = re.compile(r"\[[^\]]*\]")


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
    return re.sub(r"\s+", " ", TAG.sub(" ", text)).strip()


def norm(token: str) -> str:
    return re.sub(r"[^\wё]", "", token.lower().replace("ё", "е"))


def full_text(story: dict) -> str:
    a = story.get("anchor") or {}
    return " ".join(t.strip() for t in (a.get("intro", ""), story["narration"]["text"], a.get("outro", "")) if t.strip())


def speak(n: dict, out: Path) -> None:
    engine = n.get("engine", "piper")
    if engine == "openrouter":
        from addons.news.openrouter_voice import STYLE
        from addons.news.openrouter_voice import speak as or_speak

        or_speak(n["text"], out, n.get("voice", "ash"), n.get("model", "openai/gpt-audio-mini"), n.get("style", STYLE))
        return
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
    a, b = [norm(t) for t in script], [norm(w["word"]) for w in heard]
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
        speak({**story["narration"], "text": text}, audio)
    words = [[w, round(a + VOICE_AT, 3), round(b + VOICE_AT, 3)] for w, a, b in script_words(text, transcribe(audio))]
    speech_end = float(words[-1][2])
    duration = round(speech_end + TAIL_S, 2)
    a = story.get("anchor") or {}
    n_in, n_out = len(plain(a.get("intro", "")).split()), len(plain(a.get("outro", "")).split())
    # b-roll window: from the end of the anchor's intro to the start of the outro (the whole film without an anchor)
    b0 = round(words[n_in - 1][2] + 0.15, 3) if n_in else 0.0
    b1 = round(words[len(words) - n_out][1] - 0.1, 3) if n_out else duration
    timing = {"words": words, "speech_end": speech_end, "duration": duration, "broll": [b0, b1]}
    (A / "timing.json").write_text(json.dumps(timing, ensure_ascii=False, indent=1), encoding="utf-8")
    return timing


# -- b-roll ------------------------------------------------------------------------------------------
def slots(story: dict, window: list[float], proj: Path) -> list[dict]:
    t0, duration = window[0], window[1] - window[0]
    shots, n = story["shots"], max(1, math.ceil(duration / SHOT_S - 0.15))
    if len(shots) < n:
        print(f"warning: {len(shots)} shots for {n} slots — the last shot is held longer", flush=True)
    used: dict[str, float] = {}
    out, t = [], 0.0
    for i, s in enumerate(shots[:n]):
        d = SHOT_S if i < min(n, len(shots)) - 1 else round(duration - t, 3)
        src = (proj / s["src"]).resolve() if not Path(s["src"]).is_absolute() else Path(s["src"])
        if not src.is_file():
            sys.exit(f"shot {i}: {src} not found")
        item = {**s, "path": src, "start": round(t0 + t, 3), "dur": d, "video": src.suffix.lower() in VIDEO_EXT}
        if item["video"]:
            w, h, length = probe(src)
            a = float(s["in"]) if "in" in s else used.get(str(src), 0.3)
            if a + d > length:  # clip too short for this cut: loop back to its start
                a = max(0.0, min(0.3, length - d))
            item["in"] = round(a, 3)
            used[str(src)] = a + d
        out.append(item)
        t += d
    return out


def box_h(w: int, h: int) -> int:
    return int(min(BOX_MAX, max(BOX_MIN, W * h / w)))


def segment(item: dict, dst: Path) -> None:
    src, d = item["path"], item["dur"]
    w, h, _ = probe(src)
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
        fg = (f"[b]scale={W * 3}:{bh * 3}:force_original_aspect_ratio=increase,crop={W * 3}:{bh * 3},"
              f"zoompan=z='1+0.07*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:"
              f"s={W}x{bh}:fps={FPS}[fg]")
    flt = (f"[0:v]fps={FPS},split[a][b];{bg};{fg};[bg][fg]overlay=0:{y}:shortest=1,"
           f"trim=duration={d:.3f},setsar=1,format=yuv420p[v]")
    sh(["ffmpeg", "-v", "error", "-y", *inp, "-filter_complex", flt, "-map", "[v]", "-an", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", str(dst)])


def broll(proj: Path, items: list[dict]) -> Path:
    d = proj / "assets" / "broll"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    lst = []
    for i, it in enumerate(items):
        seg = d / f"s{i:02d}.mp4"
        segment(it, seg)
        lst.append(f"file '{seg.name}'")
    (d / "list.txt").write_text("\n".join(lst) + "\n")
    out = proj / "assets" / "broll.mp4"
    sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(d / "list.txt"), "-c", "copy", str(out)])
    return out


# -- sound bed --------------------------------------------------------------------------------------
SFX_DIRS = [Path("/opt/openmontage/sfx"), Path.home() / "Downloads" / "sfx_chp", REPO / "sfx"]
SOURCE_AUDIO = 0.35  # a news clip's own sound (sirens, crackle, crowd) under the voice


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
    snd, (b0, b1), total = story.get("sound") or {}, timing["broll"], timing["duration"]
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
    if snd.get("ambience") and b1 > b0:
        x = snd["ambience"]
        clip(find_sfx(x["src"]), b0, b1 - b0, x.get("volume", 0.22), float(x.get("offset", 0)), 0.5, 0.6, loop=True)
    if snd.get("whoosh"):
        x = snd["whoosh"]
        for t in ([b0] if b0 > 0.5 else []) + ([b1] if b1 < total - 0.5 else []):
            clip(find_sfx(x["src"]), max(0.0, t - 0.3), float(x.get("dur", 1.0)), x.get("volume", 0.35), fade_out=0.4)
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


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_workspace(proj: Path, story: dict, timing: dict, items: list[dict]) -> Path:
    ws = proj / "hyperframes"
    shutil.rmtree(ws / "assets", ignore_errors=True)
    (ws / "assets").mkdir(parents=True)
    shutil.copy2(REPO / "addons" / "mural" / "vendor" / "gsap.min.js", ws / "assets" / "gsap.min.js")
    shutil.copy2(proj / "assets" / "broll.mp4", ws / "assets" / "broll.mp4")
    shutil.copy2(proj / "assets" / "narration.mp3", ws / "assets" / "narration.mp3")
    duration = timing["duration"]
    music = dict(story.get("music") or {})
    found = find_music(music["src"]) if music.get("src") else None
    if music.get("src") and found is None:
        print(f"music {music['src']} not found — rendering without music", flush=True)
    if found:
        shutil.copy2(found, ws / "assets" / "music.mp3")
    b0, b1 = timing["broll"]
    anchor_html, anchor_js = "", ""
    a = story.get("anchor") or {}
    if a.get("dir"):
        L = anchor_dir(proj, a["dir"]) / "layers"
        layers = json.loads((L / "layers.json").read_text())
        (ws / "assets" / "anchor").mkdir()
        for f in ["base.jpg", *[f"{k}.png" for k in layers]]:
            shutil.copy2(L / f, ws / "assets" / "anchor" / f)
        imgs = "".join(f'<img id="a_{k}" class="patch" src="assets/anchor/{k}.png" alt="" '
                       f'style="left:{x}px;top:{y}px;width:{w}px;height:{h}px" />' for k, (x, y, w, h) in layers.items())
        anchor_html = (f'    <div id="anchor" class="clip" data-start="0" data-duration="{duration}" data-track-index="1">'
                       f'<div id="anchor-cam"><img id="a_base" src="assets/anchor/base.jpg" alt="" />{imgs}</div></div>')
        windows = [(0.0, b0)] + ([(b1, duration)] if b1 < duration - 0.2 else [])
        js = []
        for w0, w1 in windows:  # slow push-in while the anchor speaks
            js.append(f'  tl.fromTo("#anchor-cam", {{scale: 1.0}}, {{scale: 1.045, duration: {w1 - w0:.2f}, ease: "none"}}, {w0:.2f});')
        if "open" in layers:
            for t, st in anc.mouth_track(proj / "assets" / "narration.mp3", FPS, windows, VOICE_AT, "mid" in layers):
                js.append(f'  tl.set("#a_open", {{opacity: {int(st == "open")}}}, {t});')
                if "mid" in layers:
                    js.append(f'  tl.set("#a_mid", {{opacity: {int(st == "mid")}}}, {t});')
        if "blink" in layers:
            for t, d in anc.blink_track(windows):
                js.append(f'  tl.set("#a_blink", {{opacity: 1}}, {t}); tl.set("#a_blink", {{opacity: 0}}, {t + d:.2f});')
        anchor_js = "\n".join(js)
    caps = word_cues(timing["words"])
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
        bottom = 330 if full else H - (BOX_Y + bh // 2)
        if it.get("label"):
            chips.append(f'    <div id="chip{k}" class="clip chip label" data-layout-ignore style="bottom:{bottom + 14}px" data-start="{it["start"]}" '
                         f'data-duration="{it["dur"]}" data-track-index="4">{esc(it["label"])}</div>')
            k += 1
        if it.get("credit"):
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
    audio_js = f'  tl.to("#bgm", {{ volume: 0, duration: 1.5, ease: "power1.in" }}, {duration - 1.5:.2f});' if found else ""
    head = esc(story["headline"])
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
    #broll {{ position: absolute; left: 0; top: 0; width: {W}px; height: {H}px; object-fit: cover; z-index: 2; }}
    #anchor {{ position: absolute; inset: 0; overflow: hidden; z-index: 1; }}
    #anchor-cam {{ position: absolute; left: 0; top: 0; width: {W}px; height: {H}px; transform-origin: 50% 32%; }}
    #anchor-cam img {{ position: absolute; display: block; }}
    #a_base {{ left: 0; top: 0; width: {W}px; height: {H}px; }}
    #anchor-cam .patch {{ opacity: 0; }}
    #lower {{ position: absolute; left: 40px; right: 40px; top: 1180px; z-index: 6; }}
    #lower .tag {{ display: inline-block; background: #e3001b; color: #fff; font-size: 38px; font-weight: 900;
                   letter-spacing: 2px; padding: 9px 20px 7px; border-radius: 6px; }}
    #lower .head {{ margin-top: 12px; background: rgba(10,10,12,.88); color: #fff; font-size: 50px; line-height: 1.12;
                    font-weight: 900; padding: 18px 24px; border-left: 10px solid #e3001b; border-radius: 4px; }}
    #plate {{ position: absolute; left: 48px; right: 48px; top: 120px; z-index: 6; }}
    #plate .tag {{ display: inline-block; background: #e3001b; color: #fff; font-size: 40px; font-weight: 900;
                   letter-spacing: 2px; padding: 10px 22px 8px; border-radius: 6px; }}
    #plate .date {{ display: inline-block; margin-left: 14px; color: #fff; font-family: Arial, "DejaVu Sans", sans-serif;
                    font-weight: 700; font-size: 34px; background: rgba(10,10,12,.86); padding: 9px 16px 8px;
                    border-radius: 6px; }}
    #plate .head {{ margin-top: 18px; background: rgba(10,10,12,.86); color: #fff; font-size: 54px; line-height: 1.12;
                    font-weight: 900; padding: 20px 26px; border-left: 10px solid #e3001b; border-radius: 4px; }}
    .chip {{ position: absolute; z-index: 6; font-family: Arial, "DejaVu Sans", sans-serif; font-weight: 700; color: #fff; }}
    .chip.label {{ right: 22px; font-size: 20px; font-weight: 400; color: rgba(255,255,255,.42);
                   text-shadow: 0 1px 2px rgba(0,0,0,.35); }}
    .chip.credit {{ left: 30px; font-size: 30px; background: rgba(0,0,0,.55); padding: 5px 12px; border-radius: 4px; }}
    .cap {{ position: absolute; left: 50px; right: 50px; top: 1500px; text-align: center; z-index: 7; }}
    .cap span {{ display: inline-block; font-weight: 900; font-size: 66px; line-height: 1.15; color: #fff;
                 background: rgba(0,0,0,.55); padding: 8px 18px 10px; border-radius: 10px;
                 text-shadow: 0 2px 6px rgba(0,0,0,.6); }}
    #fade {{ position: absolute; inset: 0; background: #000; opacity: 0; pointer-events: none; z-index: 9; }}
  </style>
</head>
<body>
  <div id="root" data-composition-id="main" data-width="{W}" data-height="{H}" data-duration="{duration}">
    <video id="broll" class="clip" src="assets/broll.mp4" data-start="{b0}" data-duration="{b1 - b0:.3f}" data-track-index="0" muted playsinline></video>
{anchor_html}
{lower_html}
    <div id="plate" class="clip" data-start="{b0}" data-duration="{b1 - b0:.3f}" data-track-index="3">
      <span class="tag">{esc(story.get('tag', 'ЧП · МОСКВА'))}</span><span class="date">{esc(story.get('date', ''))}</span>
      <div class="head">{head}</div>
    </div>
{chr(10).join(chips)}
{caps_html}
    <div id="fade" class="clip" data-start="0" data-duration="{duration}" data-track-index="8"></div>
{audio}
  </div>
<script>
  window.__timelines = window.__timelines || {{}};
  const tl = gsap.timeline({{ paused: true }});
  tl.from("#plate .tag", {{ x: -60, opacity: 0, duration: 0.35, ease: "power3.out" }}, {b0 + 0.05:.2f});
  tl.from("#plate .date", {{ opacity: 0, duration: 0.3 }}, {b0 + 0.25:.2f});
  tl.from("#plate .head", {{ y: -24, opacity: 0, duration: 0.4, ease: "power3.out" }}, {b0 + 0.15:.2f});
{lower_js}
{anchor_js}
{caps_js}
{audio_js}
  tl.to("#fade", {{ opacity: 1, duration: 0.6, ease: "power1.in" }}, {duration - 0.6:.2f});
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--voice-only", action="store_true", help="voice + timings, then print how many shots are needed")
    ap.add_argument("--retime", action="store_true", help="reuse assets/narration.mp3")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()
    proj = a.project.resolve()
    story = json.loads((proj / "story.json").read_text(encoding="utf-8"))
    timing = voice(proj, story, a.retime)
    b0, b1 = timing["broll"]
    need = max(1, math.ceil((b1 - b0) / SHOT_S - 0.15))
    print(f"speech {timing['speech_end']:.1f}s, video {timing['duration']:.1f}s, shots needed {need} "
          f"(have {len(story.get('shots', []))})", flush=True)
    if a.voice_only:
        return
    items = slots(story, timing["broll"], proj)
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


if __name__ == "__main__":
    main()
