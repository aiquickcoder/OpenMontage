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


def test_digest_reads_leads_between_stories_and_gets_one_window_each():
    s = {"anchor": {"intro": "Привет.", "outro": "Пока."}, "narration": {"voice": "cedar"},
         "stories": [{"lead": "Первое.", "text": "Горит театр.", "headline": "A"},
                     {"lead": "Дальше.", "text": "Ищут девушку.", "headline": "B"}]}
    assert build.full_text(s) == "Привет. Первое. Горит театр. Дальше. Ищут девушку. Пока."
    words = [[w, i * 1.0, i * 1.0 + 0.8] for i, w in enumerate(build.full_text(s).split())]
    # «Первое.» ends at 1.8 -> b-roll 1 from 1.95 to «Дальше.» at 4.0; b-roll 2 from 4.95 to «Пока.» at 7.0
    assert build.story_windows(s, words, 9.0) == [[1.95, 3.9], [4.95, 6.9]]
    one = {"anchor": {"intro": "Привет.", "outro": "Пока."}, "narration": {"text": "Горит театр."}}
    assert build.story_windows(one, [[w, i, i + 0.8] for i, w in enumerate("Привет. Горит театр. Пока.".split())],
                               5.0) == [[0.95, 2.9]]
    assert build.windows({"broll": [1, 2]}) == [[1, 2]]
    s["stories"][0]["comment"] = "Берегите себя."
    assert build.full_text(s) == "Привет. Первое. Горит театр. Берегите себя. Дальше. Ищут девушку. Пока."
    words = [[w, i * 1.0, i * 1.0 + 0.8] for i, w in enumerate(build.full_text(s).split())]
    assert build.story_windows(s, words, 11.0)[0] == [1.95, 3.9]  # b-roll ends where the comment begins


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


def test_pick_filters_and_ranks():
    from datetime import datetime, timezone

    from addons.news import pick

    now = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)
    base = {"dt": "2026-10-24T12:00", "channels": ["moscowach"], "links": ["https://t.me/moscowach/1"], "media_got": []}
    fire = pick.judge({**base, "key": "a", "text": "Пожар на складе в Москве 😱1.2K🤬300", "media_got": ["mp4"]},
                      now, 36, set(), set())
    oddity = pick.judge({**base, "key": "b", "text": "Под Москвой нашли кривые дома"}, now, 36, set(), set())
    drones = pick.judge({**base, "key": "c", "text": "Атака БПЛА на Москву, обломки упали"}, now, 36, set(), set())
    region = pick.judge({**base, "key": "d", "channels": ["mash"], "text": "Пожар в Омске"}, now, 36, set(), set())
    done = pick.judge({**base, "key": "e", "text": "Пожар в метро Москвы"}, now, 36, {"https://t.me/moscowach/1"}, set())
    old = pick.judge({**base, "key": "f", "dt": "2026-10-22T10:00", "text": "ДТП в Москве"}, now, 36, set(), set())
    assert fire["skip"] == [] and fire["reactions"] == 1500 and fire["score"] > oddity["score"]
    assert "война/БПЛА/политика" in drones["skip"]
    assert "не Москва" in region["skip"]
    assert "уже сделано" in done["skip"]
    assert any(s.startswith("старое") for s in old["skip"])
    assert pick.clean("🔥 Пожар в Москве. BAZA в Telegram 😱358😭143") == "Пожар в Москве."


def test_sound_bed_is_exactly_film_length_with_looped_ambience(tmp_path: Path, monkeypatch):
    sfx = tmp_path / "amb.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=1", str(sfx)], check=True)
    (tmp_path / "assets").mkdir()
    monkeypatch.setattr(build, "SFX_DIRS", [tmp_path])
    story = {"sound": {"ambience": {"src": "amb.wav", "volume": 0.2}, "whoosh": {"src": "amb.wav"}}}
    timing = {"broll": [2.0, 6.0], "duration": 8.0}
    out = build.sound_bed(tmp_path, story, timing, [])
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(out)], capture_output=True, text=True).stdout)
    assert abs(dur - 8.0) < 0.05 and out.stat().st_size < 3_000_000
    level = subprocess.run(["ffmpeg", "-nostats", "-ss", "3", "-t", "2", "-i", str(out), "-af", "volumedetect", "-f",
                            "null", "-"], capture_output=True, text=True).stderr
    assert float(level.split("mean_volume:")[1].split("dB")[0]) > -40  # the ambience is really in the b-roll window
