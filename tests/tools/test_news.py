"""addons/news: script-word alignment, b-roll slots, anchor mouth/blink tracks."""
import json
import subprocess
from pathlib import Path

import numpy as np

from addons.news import anchor, build


def test_script_words_keep_script_spelling_and_fill_gaps():
    heard = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4}
             for i, w in enumerate(["в", "москве", "горит", "санаторий", "узкая"])]
    out = build.script_words("В Москве горит санаторий «Узкое».", heard)
    assert [w[0] for w in out] == ["В", "Москве", "горит", "санаторий", "«Узкое»."]
    assert out[1][1] == 0.5 and out[-1][1] >= out[-2][2]


def test_full_text_wraps_story_with_anchor():
    s = {"anchor": {"intro": "Привет.", "outro": "Пока."}, "narration": {"text": "Новость."}}
    assert build.full_text(s) == "Привет. Новость. Пока."
    assert build.full_text({"narration": {"text": "Новость."}}) == "Новость."


def test_slots_fill_broll_window_and_continue_video(tmp_path: Path, monkeypatch):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(build, "probe", lambda p: (1280, 720, 30.0))
    story = {"shots": [{"src": "a.mp4"}, {"src": "a.mp4"}, {"src": "a.mp4", "in": 20}]}
    items = build.slots(story, [6.0, 14.0], tmp_path)
    assert [i["start"] for i in items] == [6.0, 9.0, 12.0]
    assert items[-1]["dur"] == 2.0                      # last cut stretches/shrinks to the window end
    assert items[0]["in"] == 0.3 and items[1]["in"] == 3.3 and items[2]["in"] == 20


def test_mouth_track_closes_and_holds_on_twos(monkeypatch):
    env = np.array([0, 0, 1, 1, 1, 0.3, 0.3, 0, 0, 1] * 3, dtype=float)
    monkeypatch.setattr(anchor, "envelope", lambda audio, fps: env)
    tr = anchor.mouth_track(Path("x"), 10, [(0.0, 3.0)], 0.0, True)
    assert tr[0] == (0.0, "closed") and tr[-1] == (3.0, "closed")
    times = [t for t, _ in tr[1:-1]]
    assert all(b - a >= 0.2 - 1e-9 for a, b in zip(times, times[1:]))
    assert {"open", "mid"} <= {s for _, s in tr}


def test_blink_track_stays_inside_windows():
    for t, d in anchor.blink_track([(0.0, 6.0), (28.0, 33.0)]):
        assert (0 < t < 6 - d) or (28 < t < 33 - d)
