"""addons/mural: beat alignment to the transcript, word cues, narrated camera timing."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "addons" / "mural" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


narrate, build = _load("narrate"), _load("build")


def _words(text, step=0.5):
    return [{"word": " " + w, "start": i * step, "end": i * step + 0.4} for i, w in enumerate(text.split())]


def test_align_maps_beats_to_heard_words_despite_tags_and_misheard_words():
    story = {"panels": [{"id": "p1", "beat": "У меня была свадьба на Бали. [pause]"},
                        {"id": "p2", "beat": "Подруга взяла билет — [sigh] и не пришла."}]}
    heard = _words("У меня была свадьба на Бали Подруга взяла билеты и не пришла")  # "билеты" misheard
    t = narrate.align(story, heard)
    p1, p2 = t["panels"]
    assert [w[0] for w in p1["words"]] == ["У", "меня", "была", "свадьба", "на", "Бали"]
    assert p1["start"] == 0.0 and p1["end"] == p2["start"] == round(3.0 - 0.15, 3)
    assert p2["words"][-1][0] == "пришла." or p2["words"][-1][0] == "пришла"
    assert t["duration"] == round(heard[-1]["end"] + narrate.TAIL_S, 3)


def test_word_cues_split_on_punctuation_and_never_overlap():
    words = [["Я", 0.0, 0.2], ["выставила", 0.25, 0.7], ["ей", 0.75, 0.9], ["счёт.", 0.95, 1.3],
             ["В", 1.5, 1.6], ["ответ", 1.62, 1.9], ["аудит", 1.95, 2.4]]
    cues = build.word_cues(words)
    assert [c[2] for c in cues] == ["Я выставила ей счёт.", "В ответ аудит"]
    assert cues[0][1] <= cues[1][0]


def test_narrated_camera_uses_fractions_of_the_spoken_span():
    story = {"narration": {"voice": "x"}, "panels": [
        {"id": "p1", "camera": [[0, 0.5, 0.5, 1.0, None], [1.0, 0.6, 0.5, 1.2, "sine.inOut"]]},
        {"id": "p2", "camera": [[0.2, 0.5, 0.5, 1.0, "power3.inOut"], [1.0, 0.5, 0.4, 1.5, "power2.in"]],
         "shake": [0.5, 0.9, 8]}]}
    timing = {"panels": [{"id": "p1", "start": 0.0, "end": 4.0, "words": [["Раз", 0.1, 0.4]]},
                         {"id": "p2", "start": 4.0, "end": 9.0, "words": [["Два", 4.1, 4.5]]}]}
    keys, caps, shakes, duration = build.camera_keys(story, {"panel_w": 2048, "panel_h": 2048, "cols": 2, "rows": 1}, timing)
    assert [k[0] for k in keys] == [0.0, 4.0, 5.0, 9.0] and duration == 9.0
    assert keys[2][1][0] == 2048 + 0.5 * 2048  # panel 2 sits right of panel 1 in the strip
    assert shakes == [(6.5, 8.5, 8)] and [c[2] for c in caps] == ["Раз", "Два"]
