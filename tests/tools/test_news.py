"""addons/news: script-word alignment, b-roll slots, anchor mouth/blink tracks."""
import json
import subprocess
from pathlib import Path

import numpy as np

from addons.news import anchor, build


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _ai_allowed(request, monkeypatch):
    """The pick tests check topic, city and age filters on events with no media; the AI budget (0 by default since
    2026-10-29) would drop those for lack of footage, so it is lifted here unless a test is about it."""
    if "ai_budget" in request.node.name:
        return
    from addons.news import pick

    for c in pick.CITIES.values():
        monkeypatch.setitem(c, "ai_max_share", 1)


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
    s = {"anchor": {"dir": "addons/news/anchor_cat", "intro": "Привет.", "outro": "Пока."}, "narration": {"voice": "cedar"},
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
    timing = {"words": words, "windows": build.story_windows(s, words, 11.0)}
    # monitor: story 0 from its lead (not the greeting) and through its comment; story 1 from its lead
    assert build.tv_schedule(s, timing) == [(0, 0.9, 2.15), (0, 3.7, 6.05), (1, 5.9, 7.15)]


def test_slots_fill_broll_window_and_continue_video(tmp_path: Path, monkeypatch):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(build, "probe", lambda p: (1280, 720, 30.0))
    story = {"shots": [{"src": "a.mp4"}, {"src": "a.mp4"}, {"src": "a.mp4", "in": 20}]}
    items = build.slots(story, [6.0, 14.0], tmp_path)
    assert [i["start"] for i in items] == [6.0, 8.667, 11.333]  # no words: the window is split evenly
    assert round(sum(i["dur"] for i in items), 3) == 8.0
    assert items[0]["in"] == 0.3 and items[1]["in"] == 2.967 and items[2]["in"] == 20


def test_cuts_land_between_words_and_prefer_sentence_ends():
    # window 0..9, three shots: targets 3.0 and 6.0
    words = [["Раз", 0.1, 0.5], ["два", 0.6, 1.0], ["три.", 1.1, 2.6], ["Четыре", 3.0, 3.6], ["пять", 3.7, 5.7],
             ["шесть", 5.75, 6.3], ["семь.", 6.35, 8.9]]
    cuts = build.cut_points([0.0, 9.0], 3, words, 5.95)
    assert cuts[0] == 2.8          # the pause after «три.» (2.6–3.0), not 3.0 inside nothing — a sentence end
    assert cuts[1] == 5.725        # «пять» runs 3.7–5.7: the cut goes to the boundary after it, not inside «шесть»
    for c in cuts:
        assert not any(a < c < b for _, a, b in words)


def test_mouth_track_closes_and_holds_on_twos(monkeypatch):
    # 0.5 right after a peak is half-open; a drop to 0.3 there now closes the mouth (syllable gaps, 2026-10-29)
    env = np.array([0, 0, 1, 1, 1, 0.5, 0.5, 0, 0, 1] * 3, dtype=float)
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


def test_pick_city_geo_from_cities_json():
    from datetime import datetime, timezone

    from addons.news import pick

    now = datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)
    e = {"dt": "2026-10-26T12:00", "links": [], "media_got": []}
    local = pick.judge({**e, "key": "a", "channels": ["tatarstansos"], "text": "ДТП на тракте, пострадали двое"},
                       now, 36, set(), set(), "kazan")
    federal_kzn = pick.judge({**e, "key": "b", "channels": ["mash"], "text": "Пожар в Казани на складе"},
                             now, 36, set(), set(), "kazan")
    federal_msk = pick.judge({**e, "key": "c", "channels": ["mash"], "text": "Пожар в Москве на складе"},
                             now, 36, set(), set(), "kazan")
    assert local["skip"] == [] and federal_kzn["skip"] == [] and federal_msk["skip"] == ["не Казань"]
    assert pick.judge({**e, "key": "d", "channels": ["moscowach"], "text": "Пожар на складе"}, now, 36, set(), set())["skip"] == []
    spb = [pick.judge({**e, "key": k, "channels": ["mash"], "text": t}, now, 36, set(), set(), "spb")["skip"]
           for k, t in (("e", "В Петербурге задержали поджигателя"), ("f", "ДТП в Гатчине, пострадали двое"),
                        ("g", "Авария на Ленинградском шоссе в Москве"))]
    assert spb == [[], ["не Петербург"], ["не Петербург"]]  # Ленобласть is not «ЧП Петербург», as Подмосковье is not Moscow
    assert pick.judge({**e, "key": "h", "channels": ["fontankaspb"], "text": "Пожар на складе"}, now, 36, set(), set(),
                      "spb")["skip"] == []


def test_topics_tag_crimes_and_detentions_from_word_starts():
    from addons.news import topics

    assert topics.tags("Полиция задержала подозреваемого") == ["crime", "detention"]
    assert topics.tags("Перестрелка в Химках, следователи возбудили дело")[0] == "crime"
    assert topics.tags("Двое погибли в ДТП") == ["accident", "dtp", "fatal"]
    for calm in ("Вследствие дождей размыло дорогу", "Новая посуда", "Задержка рейса на три часа",
                 "Девушка решила испытать судьбу", "Тараканы захватили общагу", "Захватывающий матч",
                 "Харри Кейн — лучший нападающий года", "Суд приостановил работу трёх кафе",
                 "Мосгорсуд удовлетворил иск телеканала", "Около 100 рейсов задержаны в Шереметьево",
                 "Рейс Москва-Мурманск задержали на 14 часов"):
        assert topics.tags(calm) == ["other"], calm
    assert topics.tags("Судья отправил его в СИЗО")[:2] == ["crime", "detention"]
    assert topics.tags("В суде огласили приговор") == ["crime"]
    assert topics.is_event("Мошенники обманули пенсионерку") and not topics.is_event("Открыли новый парк")


def test_pick_weights_crime_and_can_keep_only_it():
    from datetime import datetime, timezone

    from addons.news import pick

    now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    e = {"dt": "2026-10-27T10:00", "channels": ["moscowach"], "links": [], "media_got": ["photo"]}
    arrest = pick.judge({**e, "key": "a", "text": "В Москве задержали мужчину, который напал на прохожего"}, now, 36, set(), set())
    theft = pick.judge({**e, "key": "b", "text": "В Москве украли самокат у подъезда"}, now, 36, set(), set())
    oddity = pick.judge({**e, "key": "c", "text": "В Москве открыли новый парк"}, now, 36, set(), set())
    assert arrest["tags"][:2] == ["crime", "detention"] and arrest["score"] > theft["score"] > oddity["score"]
    assert pick.judge({**e, "key": "c", "text": "Пожар на складе"}, now, 36, set(), set(), crime_only=True)["skip"] == ["не криминал"]
    assert "не ЧП" in pick.judge({**e, "key": "h", "text": "Харри Кейн — лучший нападающий года, Москва"}, now, 36, set(),
                                 set())["skip"]
    political = pick.judge({**e, "key": "d", "text": "Суд арестовал политолога"}, now, 36, set(), set())
    assert "война/БПЛА/политика" in political["skip"]
    dam = pick.judge({**e, "key": "e", "channels": ["mash_iptash"], "text": "Гонщика с Кремлёвской дамбы лишили прав"},
                     now, 36, set(), set(), "kazan")
    assert dam["skip"] == []
    for crime in ("Казанца задержали за передачу мошенникам денег, санкция статьи — до десяти лет",
                  "В Казанском кремле нашли коррупционные нарушения"):
        assert pick.judge({**e, "key": "f", "channels": ["tatarstansos"], "text": crime}, now, 36, set(), set(),
                          "kazan")["skip"] == [], crime
    for politics in ("Евросоюз ввёл санкции против главы республики", "В Кремле заявили о новых мерах"):
        assert "война/БПЛА/политика" in pick.judge({**e, "key": "g", "text": politics}, now, 36, set(), set())["skip"]
    for child in ("Учителя подозревают в убийстве двухлетнего ребёнка и его бабушки",
                  "Задержан подозреваемый в убийстве женщины и ребенка", "Мужчина напал на 15-летнюю школьницу"):
        assert "дети-жертвы" in pick.judge({**e, "key": "h", "channels": ["fontankaspb"], "text": child}, now, 36,
                                           set(), set(), "spb")["skip"], child
    assert pick.judge({**e, "key": "i", "channels": ["fontankaspb"], "text": "Задержан подозреваемый в убийстве соседа"},
                      now, 36, set(), set(), "spb")["skip"] == []
    vyborg = "В Выборгском районе Петербурга наряд Росгвардии задержал женщину, находившуюся в розыске"
    assert pick.judge({**e, "key": "j", "channels": ["fontankaspb"], "text": vyborg}, now, 36, set(), set(), "spb")["skip"] == []
    assert "война/БПЛА/политика" in pick.judge({**e, "key": "k", "channels": ["fontankaspb"],
                                                "text": "Наблюдателя на выборах задержали"}, now, 36, set(), set(), "spb")["skip"]


def test_pick_keeps_crashes_with_hurt_children_and_scams_that_mention_war():
    from datetime import datetime, timezone

    from addons.news import pick

    now = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
    e = {"dt": "2026-10-27T10:00", "links": [], "media_got": ["photo"], "channels": ["tatarstansos"]}
    crash = "8-летний ребенок пострадал в массовом ДТП на проспекте Универсиады в Казани, столкнулись пять машин"
    assert pick.judge({**e, "key": "a", "text": crash}, now, 36, set(), set(), "kazan")["skip"] == []
    assert "дети-жертвы" in pick.judge({**e, "key": "b", "text": "Двухлетний ребёнок погиб в ДТП на трассе"},
                                       now, 36, set(), set(), "kazan")["skip"]
    scam = ("Мошенники стали чаще обманывать татарстанцев под предлогом «замены домофона»: лжесотрудники пугают "
            "незаконными переводами, в том числе для нужд ВСУ, и забирают деньги через курьеров")
    assert pick.judge({**e, "key": "c", "text": scam}, now, 36, set(), set(), "kazan")["skip"] == []
    assert "война/БПЛА/политика" in pick.judge({**e, "key": "d", "text": "ВСУ атаковали дроны над Татарстаном, пожар"},
                                               now, 36, set(), set(), "kazan")["skip"]


def test_pick_fill_lists_uncovered_older_incidents(tmp_path):
    import json
    import subprocess
    import sys
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    at = lambda h: (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M")  # noqa: E731
    vid = tmp_path / "v.mp4"  # a story needs footage of its own: the AI budget is 0 (2026-10-29)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=64x64:d=9", str(vid)], check=True)
    own = {"media_got": ["mp4"], "media": [{"type": "mp4", "local": str(vid)}]}
    events = [
        {"key": "fresh1", "dt": at(3), "channels": ["tatarstansos"], "links": [], "text": "Пожар на складе в Казани", **own},
        {"key": "old_det", "dt": at(80), "channels": ["tatarstansos"], "links": [],
         "text": "В Казани задержали мужчину, угрожавшего ножом продавцу", **own},
        {"key": "old_calm", "dt": at(80), "channels": ["tatarstansos"], "links": [], "text": "В Казани открыли новый сквер"},
        {"key": "too_old", "dt": at(24 * 9), "channels": ["tatarstansos"], "links": [], "text": "В Казани задержали вора"},
    ]
    (tmp_path / "base.json").write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    p = subprocess.run([sys.executable, "-I", "addons/news/pick.py", "--city", "kazan", "--base", str(tmp_path),
                        "--no-harvest", "--hours", "30"], capture_output=True, text=True, check=True)
    out = json.loads((tmp_path / "candidates.json").read_text(encoding="utf-8"))
    assert [c["key"] for c in out["candidates"]] == ["fresh1"]
    assert [c["key"] for c in out["fill"]] == ["old_det"]  # older but uncovered; not the calm one, not 9 days old
    assert "добор" in p.stdout


def test_build_refuses_post_footage_that_was_not_cleaned():
    from addons.news import build

    story = {"stories": [{"shots": [
        {"src": "media/clip.mp4", "credit": "Видео: @moscowach"},
        {"src": "media/clean/clip_3.clean.mp4", "src_orig": "media/clip.mp4"},
        {"src": "media/place.jpg", "credit": "Фото: Автор / Wikimedia, CC BY 4.0"},
        {"src": "media/post.jpg", "clean": "none"},
        {"src": "illustrations/i1.png", "label": "ИИ-иллюстрация"}]}]}
    assert build.unclean_footage(story) == ["media/clip.mp4"]
    assert build.unclean_footage({**story, "footage_clean": False}) == []


def test_script_words_keeps_a_dash_next_to_its_neighbours():
    from addons.news import build

    heard = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate(
        ["за", "неделю", "в", "татарстане", "шесть", "таких", "случаев", "—", "никому", "не", "называйте", "коды"])]
    out = build.script_words("За неделю в Татарстане — шесть таких случаев. Никому не называйте коды", heard)
    dash = next(w for w in out if w[0] == "—")
    assert 1.5 <= dash[1] <= dash[2] <= 2.1  # between «Татарстане» (1.5–1.9) and «шесть» (2.0), not at the far «—»


def test_word_cues_never_leave_a_lone_dash_or_an_empty_cue():
    from addons.mural.build import word_cues

    words = [["За", 55.0, 55.3], ["неделю", 55.3, 55.7], ["в", 55.7, 55.8], ["Татарстане", 56.2, 56.82],
             ["—", 56.82, 57.02], ["шесть", 56.82, 57.18], ["таких", 57.2, 57.5], ["случаев.", 57.5, 58.0]]
    cues = word_cues(words)
    assert all(b - a >= 0.05 for a, b, _ in cues)
    assert not any(t.strip() == "—" for _, _, t in cues)
    assert cues[0][2].endswith("Татарстане —")


def test_voice_text_drops_cues_but_keeps_pauses_between_stories():
    s = {"anchor": {"dir": "addons/news/anchor_cat", "intro": "Привет.", "outro": "Пока."},
         "narration": {"engine": "fishaudio"},
         "stories": [{"lead": "Первое.", "text": "[тревожно] Нашли <stress>двенадцать</stress>, пачек.",
                      "comment": "[мягко] Берегите себя."},
                     {"lead": "Второе.", "text": "Ищут девушку."}]}
    assert build.plain(build.full_text(s)) == "Привет. Первое. Нашли двенадцать, пачек. Берегите себя. Второе. Ищут девушку. Пока."
    assert build.voice_text(s) == ("Привет. Первое. Нашли двенадцать, пачек. Берегите себя. [pause] Второе. Ищут "
                                   "девушку. [pause] Пока.")  # owner 2026-10-29: no tone cues, no <stress>
    words = [[w, i * 1.0, i * 1.0 + 0.8] for i, w in enumerate(build.plain(build.full_text(s)).split())]
    assert build.story_windows(s, words, 14.0)[0] == [1.95, 4.9]  # cues do not shift the word count


def test_a_long_word_moves_the_cut_further_than_the_snap():
    # «огнетушителей» read for 1.56 s right over the 3.0 s target (Россия 28.10): no boundary within ±0.6 s
    words = [["шести", 1.2, 2.0], ["огнетушителей", 2.0, 3.65], ["с", 3.65, 3.8], ["полок", 3.8, 5.6]]
    cut = build.cut_points([0.0, 6.0], 2, words, 5.95)[0]
    assert cut == 2.0 and not any(a < cut < b for _, a, b in words)  # 1 s early, inside the 1.8–3.35 s shot


def _timed(text: str, t0: float = 0.0, word: float = 0.42, gap: float = 0.08, pause: float = 0.35) -> list:
    out, t = [], t0
    for w in text.split():
        out.append([w, round(t, 3), round(t + word, 3)])
        t += word + gap + (pause if w[-1] in ".," else 0)
    return out


def test_beats_follow_sentences_and_split_long_ones_at_a_comma():
    ws = _timed("Во Фрунзенском районе игрок ударил соперника и сбежал. Росгвардейцы задержали его у "
                "спорткомплекса, а пострадавший в больнице с травмой головы. Возбуждено дело.", t0=10.0)
    win = [9.9, ws[-1][2] + 0.6]
    bs = build.beats(ws, win)
    assert bs[0]["start"] == 9.9 and bs[-1]["end"] == win[1]
    assert bs[0]["text"].startswith("Во Фрунзенском")
    assert any(b["text"].startswith("Росгвардейцы") for b in bs)          # a sentence opens its own shot
    assert any(b["text"].startswith("а пострадавший") for b in bs)        # the long one splits at «, а»
    for b in bs[1:]:                                   # the cut comes just before the first word, never inside a word
        k = next(i for i, w in enumerate(ws) if w[0] == b["text"].split()[0])
        assert ws[k - 1][2] <= b["start"] <= ws[k][1] and b["start"] >= ws[k][1] - build.BEAT_LEAD
    for b in bs[:-1]:
        assert build.BEAT_MIN <= b["end"] - b["start"] <= build.BEAT_HARD
    assert [b["end"] for b in bs[:-1]] == [b["start"] for b in bs[1:]]


def test_a_short_phrase_is_merged_and_slots_cut_on_phrases(tmp_path: Path, monkeypatch):
    ws = _timed("Пожар потушили. Причина неизвестна, проверка идёт.", t0=2.0)
    bs = build.beats(ws, [1.9, ws[-1][2] + 0.5])
    # «Пожар потушили.» is 1.3 s, but merged with the next sentence it would run 4.2 s: it stays a short shot
    assert [b["text"] for b in bs] == ["Пожар потушили.", "Причина неизвестна, проверка идёт."]
    ws2 = _timed("Пожар потушили. Причина неизвестна.", t0=2.0)
    assert len(build.beats(ws2, [1.9, ws2[-1][2] + 0.3])) == 1     # 2.6 s together: merged into one phrase
    (tmp_path / "a.png").write_bytes(b"x")
    story = {"shots": [{"src": "a.png"} for _ in bs]}
    items = build.slots(story, [1.9, ws[-1][2] + 0.5], tmp_path, ws, bs)
    assert [i["start"] for i in items] == [b["start"] for b in bs]


def test_phrases_keep_names_in_quotes_and_prepositions_whole():
    ws = _timed("Лидера ОПГ «Хади Такташ», отбывавшего пожизненный срок, тайно доставили в казанский СИЗО.", t0=1.0)
    texts = [b["text"] for b in build.beats(ws, [0.9, ws[-1][2] + 0.5])]
    assert not any(t.endswith("«Хади") for t in texts)
    ws = _timed("Причина пока не установлена, но всё уже локализовали.", t0=1.0, word=0.5)
    texts = [b["text"] for b in build.beats(ws, [0.9, ws[-1][2] + 0.5])]
    assert not any(t.endswith(("пока", "но", "в")) for t in texts)


def test_cleaning_needs_a_look_where_the_auto_mask_is_blind():
    from addons.news import clean_footage as cf

    assert cf.needs_look(0.3, 0.0)            # a still camera
    assert cf.needs_look(2.1, 1.0)            # a slow camera: buildings and «фонтанка.ру» merged into one patch
    assert not cf.needs_look(3.0, 0.0)
    auto = np.zeros((20, 20), np.uint8)
    auto[0:2, 0:2] = 255
    added = cf.hand_mask((20, 20), {"add": [[10, 10, 3, 3]]}, auto)
    assert added[0, 0] == 255 and added[11, 11] == 255          # {"add": …} keeps the auto mask
    replaced = cf.hand_mask((20, 20), [[10, 10, 3, 3]], auto)
    assert replaced[0, 0] == 0 and replaced[11, 11] == 255      # a plain list replaces it
    assert not cf.hand_mask((20, 20), {"add": []}, np.zeros((20, 20), np.uint8)).any()


def test_cleaned_pieces_are_as_long_as_their_phrases(tmp_path: Path):
    from addons.news import clean_footage as cf

    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "beats.json").write_text(json.dumps(
        [[{"start": 5.0, "end": 7.2, "text": "a"}, {"start": 7.2, "end": 11.0, "text": "b"}]]))
    story = {"stories": [{"shots": [{"src": "media/a.mp4", "in": 2}, {"src": "media/a.mp4", "in": 9},
                                    {"src": "media/a.mp4", "in": 20}]}]}
    use = cf.footage(tmp_path, story)
    assert use["media/a.mp4@2"]["len"] == 2.5 and use["media/a.mp4@9"]["len"] == 4.1
    assert "media/a.mp4@20" not in use                          # past the last phrase: not shown, not cleaned
    (tmp_path / "assets" / "beats.json").unlink()
    assert cf.footage(tmp_path, story)["media/a.mp4@2"]["len"] == cf.SHOT_S


def test_a_photo_and_a_clip_with_one_name_keep_separate_masks():
    from addons.news import clean_footage as cf

    assert cf.tag("media/door.mp4") == "door" and cf.tag("media/door.jpg") == "door-jpg"


def test_picture_warnings_catch_repeats_and_borrowed_pictures():
    s = {"stories": [{"shots": [{"src": "media/metro.jpg"}, {"src": "media/a.mp4", "in": 1}]},
                     {"shots": [{"src": "media/court.jpg"}, {"src": "media/court.jpg"}, {"src": "media/metro.jpg"},
                                {"src": "media/court.jpg"}]}]}
    w = build.picture_warnings(s)
    assert any("court.jpg is shown 3 times" in x for x in w)
    assert any("court.jpg twice in a row" in x for x in w)
    assert any("metro.jpg is shown in stories [1, 2]" in x for x in w)
    chopped = {"stories": [{"shots": [{"src": "media/a.mp4", "in": 1}, {"src": "media/a.mp4", "in": 4},
                                      {"src": "media/a.mp4", "in": 7}]}]}
    assert build.picture_warnings(chopped) == [             # owner 2026-10-30: footage stays whole
        "story 1: a.mp4 is cut into pieces — let the clip run as one shot over its phrases"]
    skip_dark = {"stories": [{"shots": [{"src": "media/a.mp4", "in": 0}, {"src": "media/a.mp4", "in": 19.5}]}]}
    assert build.picture_warnings(skip_dark) == []          # two long pieces in order: an edit, not chopping
    back = {"stories": [{"shots": [{"src": "media/a.mp4", "in": 9}, {"src": "media/b.jpg"}, {"src": "media/a.mp4", "in": 2}]}]}
    assert any("jumps back in time" in x for x in build.picture_warnings(back))
    ok = {"stories": [{"shots": [{"src": "media/a.mp4", "in": 1}, {"src": "media/b.jpg"}, {"src": "media/a.mp4", "in": 9}]}]}
    assert build.picture_warnings(ok) == []                 # a clip, a photo, the clip again later on


def test_storyboard_puts_each_picture_next_to_its_words(tmp_path: Path):
    import cv2

    cv2.imwrite(str(tmp_path / "a.png"), np.full((90, 160, 3), 128, np.uint8))
    story = {"stories": [{"headline": "Суд", "shots": [{"src": "a.png"}, {"src": "missing.png"}]}]}
    items = [{"w": 0, "path": tmp_path / "a.png", "video": False, "start": 0.0, "dur": 2.0, "src": "a.png"},
             {"w": 0, "path": None, "video": False, "start": 2.0, "dur": 2.0, "src": "missing.png"}]
    timing = {"words": [["Красносельский", 0.1, 0.8], ["суд", 0.9, 1.2], ["отправил", 2.1, 2.6]]}
    out = build.storyboard(tmp_path, story, items, timing)
    assert [p.name for p in out] == ["storyboard_1.jpg"] and cv2.imread(str(out[0])).shape[1] == 1200


def test_a_fast_narrator_still_closes_the_mouth_between_syllables(monkeypatch):
    # level stays high (0.6–1.0) through fast speech: whole-take thresholds kept it open; local ones close on dips
    env = np.array([0.2, 1.0, 1.0, 0.6, 1.0, 1.0, 0.6, 1.0, 1.0, 0.6] * 4, dtype=float)
    monkeypatch.setattr(anchor, "envelope", lambda audio, fps: env)
    tr = anchor.mouth_track(Path("x"), 30, [(0.0, 40 / 30)], 0.0, True)
    assert sum(1 for _, s in tr[1:-1] if s == "mid") >= 4      # every dip shows, not a mouth hanging open


def test_a_checked_post_picture_left_out_is_flagged(tmp_path: Path):
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "wm.json").write_text(json.dumps({"baumana.jpg": [[0, 0, 10, 10]], "fire.mp4": {"add": []}}))
    story = {"stories": [{"shots": [{"src": "illustrations/b1.png"}, {"src": "media/clean/fire_0.clean.mp4",
                                                                     "src_orig": "media/fire.mp4"}]}]}
    w = build.unused_post_media(tmp_path, story)
    assert len(w) == 1 and w[0].startswith("baumana.jpg")


def test_ai_budget_zero_drops_stories_without_footage_and_favours_rich_ones(tmp_path: Path, monkeypatch):
    from datetime import datetime, timezone

    from addons.news import pick

    monkeypatch.setitem(pick.CITIES["moscow"], "ai_max_share", 0)
    vid = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=64x64:d=9", str(vid)], check=True)
    now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    base = {"dt": "2026-10-29T10:00", "channels": ["moscowach"], "links": []}
    bare = pick.judge({**base, "key": "a", "text": "Пожар на складе в Москве"}, now, 36, set(), set())
    rich = pick.judge({**base, "key": "b", "text": "Пожар на складе в Москве", "media_got": ["mp4"],
                       "media": [{"type": "mp4", "local": str(vid)}]}, now, 36, set(), set())
    assert "нет своих кадров (ИИ выключен)" in bare["skip"]
    assert rich["skip"] == [] and rich["visual"] == "rich" and rich["video_s"] >= 8
    assert pick.describe(rich) == "видео 9 с"
    monkeypatch.setitem(pick.CITIES["moscow"], "ai_max_share", 0.3)   # some AI allowed: kept, but below footage
    bare = pick.judge({**base, "key": "a", "text": "Пожар на складе в Москве"}, now, 36, set(), set())
    rich = pick.judge({**base, "key": "b", "text": "Пожар на складе в Москве", "media_got": ["mp4"],
                       "media": [{"type": "mp4", "local": str(vid)}]}, now, 36, set(), set())
    assert bare["skip"] == [] and rich["score"] > bare["score"]


def test_ai_budget_counts_illustrations_against_the_city_limit():
    s = {"city": "kazan", "stories": [{"shots": [{"src": "media/clean/a_0.clean.mp4"},
                                                  {"src": "illustrations/k1.png", "label": "ИИ-иллюстрация"}]}]}
    assert build.ai_budget(s) == (1, 2, 0)                 # no AI pictures at all (owner 2026-10-30; 30% before)
    assert build.ai_budget({**s, "ai_max_share": 0.5})[2] == 0.5


def test_crops_of_one_photo_are_different_shots():
    s = {"stories": [{"shots": [{"src": "media/p.jpg"}, {"src": "media/p.jpg", "crop": [0, 0, 400, 300]},
                                {"src": "media/p.jpg", "crop": [200, 100, 400, 300]}]}]}
    assert build.picture_warnings(s) == []                  # a wide view and two details, not one photo three times


def test_maps_become_shots_once(tmp_path: Path, monkeypatch):
    from addons.news import visuals

    made_maps = []
    monkeypatch.setattr(visuals, "map_clip", lambda spec, out: made_maps.append(spec) or out.write_bytes(b"x") or out)
    story = {"stories": [{"shots": [{"map": {"street": "улица Баумана", "city": "Казань"}}, {"src": "media/b.mp4"}]}]}
    assert visuals.resolve(tmp_path, story) == 1
    assert story["stories"][0]["shots"][0]["src"].startswith("assets/visuals/map_")
    assert visuals.resolve(tmp_path, story) == 1 and len(made_maps) == 1     # made once, then reused


def _clip(path: Path, seconds: float = 2.0) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size=320x180:rate=30:duration={seconds}", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _no_cat_digest() -> dict:
    return {"anchor": {"intro": "Привет.", "outro": "Пока."}, "narration": {"voice": "cedar"},
            "stories": [{"lead": "Первое.", "text": "Горит театр.", "comment": "Берегите себя.", "headline": "A"},
                        {"lead": "Дальше.", "text": "Ищут девушку.", "comment": "Звоните.", "headline": "B"}]}


def test_without_the_cat_one_voice_reads_no_comments_and_pictures_run_through_the_leads():
    s = _no_cat_digest()
    assert build.narrator(s)
    assert build.full_text(s) == "Привет. Первое. Горит театр. Дальше. Ищут девушку. Пока."
    cat = {**s, "anchor": {**s["anchor"], "dir": "addons/news/anchor_cat"}}
    assert not build.narrator(cat) and "Берегите себя." in build.full_text(cat)
    words = [[w, i * 1.0, i * 1.0 + 0.8] for i, w in enumerate(build.full_text(s).split())]
    text = build.story_windows(s, words, 9.0)
    assert text == [[1.95, 3.9], [4.95, 6.9]]
    t = build.narrator_windows(s, words, 9.0, text)
    # story 1 from 0 (the greeting, its lead), story 2 from its lead «Дальше.» to the end (the outro)
    assert t["windows"] == [[0.0, 3.9], [3.9, 9.0]] and t["text"] == text and t["intro"] == [0.0, 0.9]
    assert build.text_windows(t) == text and build.text_windows({"windows": [[1, 2]]}) == [[1, 2]]


def test_without_the_cat_stills_cover_the_greeting_the_leads_and_the_outro(tmp_path: Path):
    s = _no_cat_digest()
    (tmp_path / "a.png").write_bytes(b"x")
    _clip(tmp_path / "v.mp4")
    s["stories"][0]["shots"] = [{"src": "a.png"}]
    s["stories"][1]["shots"] = [{"src": "v.mp4"}]
    words = [[w, i * 1.0, i * 1.0 + 0.8] for i, w in enumerate(build.full_text(s).split())]
    timing = {"words": words, "duration": 9.0,
              **build.narrator_windows(s, words, 9.0, build.story_windows(s, words, 9.0))}
    items = build.film_items(tmp_path, s, timing, [[], []])
    # the greeting over a teaser still; a picture is held from its lead; a clip's lead and the outro get its frames
    assert [it.get("card") for it in items] == ["intro", None, "lead", None, "outro"]
    for w, (v0, v1) in enumerate(timing["windows"]):
        mine = [it for it in items if it["w"] == w]
        assert mine[0]["start"] == v0 and round(sum(it["dur"] for it in mine), 3) == round(v1 - v0, 3)
    assert items[1]["path"].name == "a.png" and items[1]["start"] == 0.9 and items[1]["dur"] == 3.0
    lead, clip, outro = items[2:]
    assert lead["zoom"] == [1.07, 1.0] and lead["path"].suffix == ".jpg" and lead["path"].is_file()
    assert clip["video"] and outro["path"].is_file() and outro["path"] != lead["path"]


def test_a_new_voice_keeps_cuts_on_the_phrases_the_shots_were_planned_for():
    ws = _timed("Во Фрунзенском районе игрок ударил соперника. Росгвардейцы задержали его, а пострадавший в "
                "больнице — у него травма.", t0=2.0, word=0.55)
    win = [1.9, ws[-1][2] + 0.5]
    shots = [{"src": "a", "beat": "Во Фрунзенском районе"}, {"src": "b", "beat": "Росгвардейцы задержали"},
             {"src": "c", "beat": "а пострадавший в"}, {"src": "d", "beat": "— у него тра"}]  # cut short
    bs = build.shot_beats(shots, ws, win)
    assert [b["text"].split()[0] for b in bs] == ["Во", "Росгвардейцы", "а", "—"]
    assert bs[0]["start"] == 1.9 and bs[-1]["end"] == win[1]
    assert bs[1]["start"] == round(max(ws[5][2], ws[6][1] - build.BEAT_LEAD), 3)
    assert [b["end"] for b in bs[:-1]] == [b["start"] for b in bs[1:]]
    story = {"narration": {"text": " ".join(w[0] for w in ws)}, "shots": shots}
    assert build.story_beats(story, {"words": ws, "windows": [win]}) == [bs]
    assert build.shot_beats(shots[1:], ws, win) is None                       # the first shot opens the story
    assert build.shot_beats(shots[:1] + [{"src": "x", "beat": "Нет такого"}], ws, win) is None
    assert build.shot_beats([{"src": "a"}], ws, win) is None                  # no beats: phrases as before
    assert build.story_beats({**story, "shots": [{"src": "a"}]}, {"words": ws, "windows": [win]}) == [
        build.beats(ws, win)]


def test_a_clip_shorter_than_its_cut_holds_its_last_frame(tmp_path: Path):
    out = tmp_path / "s.mp4"
    build.segment({"path": _clip(tmp_path / "v.mp4", 1.0), "dur": 1.6, "video": True, "in": 0.0}, out)
    assert abs(build.probe(out)[2] - 1.6) < 0.1
    png = tmp_path / "p.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180", "-frames:v", "1",
                    str(png)], check=True)
    build.segment({"path": png, "dur": 0.5, "video": False, "zoom": [1.07, 1.0]}, tmp_path / "p.mp4")
    assert abs(build.probe(tmp_path / "p.mp4")[2] - 0.5) < 0.1


def test_without_the_cat_a_map_can_go_under_a_lead(tmp_path: Path):
    s = _no_cat_digest()
    (tmp_path / "a.png").write_bytes(b"x")
    _clip(tmp_path / "v.mp4")
    _clip(tmp_path / "map.mp4", 6.0)
    s["stories"][0]["shots"] = [{"src": "a.png"}]
    s["stories"][1]["shots"] = [{"src": "v.mp4"}]
    s["stories"][1]["lead_shot"] = {"map": {"street": "улица Нурсултана Назарбаева", "city": "Казань"},
                                    "src": "map.mp4"}
    words = [[w, i * 1.0, i * 1.0 + 0.8] for i, w in enumerate(build.full_text(s).split())]
    timing = {"words": words, "duration": 9.0,
              **build.narrator_windows(s, words, 9.0, build.story_windows(s, words, 9.0))}
    lead = next(it for it in build.film_items(tmp_path, s, timing, [[], []]) if it["w"] == 1)
    assert lead["card"] == "lead" and lead["path"].name == "map.mp4" and lead["video"] and lead["in"] == 0.0
    assert (lead["start"], lead["dur"]) == (3.9, 1.05)                 # «Дальше.» over the map, then the story's clip


def test_a_thesis_sits_on_its_picture_and_the_captions_give_way(tmp_path: Path):
    assert build.thesis_markup("Задержаны *трое*|под стражей") == "Задержаны <em>трое</em><br>под стражей"
    assert "<em>&lt;b&gt;</em>" in build.thesis_markup("*<b>*")     # the text is escaped, only the stars mark
    v = _clip(tmp_path / "v.mp4")                                    # 320x180: a box 760 high around BOX_Y
    items = [{"path": v, "video": True, "start": 2.0, "dur": 2.0, "w": 0, "thesis": "*20 флаконов* в багаже"},
             {"path": v, "video": True, "start": 4.0, "dur": 2.0, "w": 0},
             {"path": v, "video": True, "start": 6.0, "dur": 2.0, "w": 0},
             {"path": v, "video": True, "start": 8.0, "dur": 1.5, "w": 0, "thesis": "Конец", "span": 3},
             {"path": v, "video": True, "start": 12.0, "dur": 2.0, "w": 1}]
    html, js, spans = build.theses(items)
    assert spans == [(2.0, 6.0), (8.0, 9.5)]            # two cuts by default; never into the next story
    assert 'data-start="2.0" data-duration="4.0"' in html[0]
    assert f"bottom:{build.H - (build.BOX_Y + 380)}px" in html[0] and "<em>20 флаконов</em>" in html[0]
    caps = [(1.0, 2.4, "до"), (2.5, 3.0, "под тезисом"), (4.5, 5.0, "всё ещё под ним"), (6.0, 6.5, "после")]
    assert build.around(caps, spans) == [(1.0, 2.0, "до"), (6.0, 6.5, "после")]


def test_thesis_warnings_keep_them_short_and_few():
    story = {"stories": [{"shots": [{"thesis": "*Трое* под стражей"}, {"thesis": "x" * 50},
                                    {"thesis": "a"}, {"thesis": "b"}]},
                         {"shots": [{}, {"thesis": "c"}]}]}
    w = build.thesis_warnings(story)
    assert any("50 characters" in x for x in w) and any("4 theses" in x for x in w)
    assert any(x.startswith("story 2: no thesis on its first shot") for x in w)


def test_official_footage_marked_clean_is_not_sent_to_the_cleaner(tmp_path: Path):
    from addons.news import clean_footage as cf

    story = {"stories": [{"shots": [{"src": "media/mvd.mp4", "in": 1, "clean": "none"}, {"src": "media/post.mp4"}]}]}
    assert list(cf.footage(tmp_path, story)) == ["media/post.mp4@0"]


def test_an_article_gives_its_text_date_and_own_video_not_the_live_channels():
    from addons.news import article_video as av

    page = ('<meta property="og:title" content="Задержали пятерых" /><nav>Онлайн трансляция</nav>'
            '<source src="https://planeta.mediacdn.ru/cdn/tnvplanet/playlist.m3u8">'
            '<h1>Задержали пятерых</h1><p class="d">29 октября 2026, 14:38</p><div>В&nbsp;Ульяновской области '
            'задержали пятерых.<br><br>Ущерб — 25 млн.<br>https://tnv.ru</div><div>Читайте новости в нашем Telegram</div>'
            '<video src="https://cdn.example.ru/news/584371.mp4"></video>')
    a = av.article(page, "https://tnv.ru/x")
    assert a["title"] == "Задержали пятерых" and a["date"] == "29 октября 2026 14:38"
    assert a["text"] == "В Ульяновской области задержали пятерых. Ущерб — 25 млн."
    assert av.video_source(page) == ("mp4", "https://cdn.example.ru/news/584371.mp4")


def test_a_site_section_becomes_posts_with_their_own_video(tmp_path: Path, monkeypatch):
    from addons.news import sources

    pages = {
        "https://tnv.ru/news/the-incident/": '<a href="/news/the-incident/584371-zaderzhali/">a</a>'
                                             '<a href="/news/the-incident/2/">page 2</a><a href="/about/">x</a>',
        "https://tnv.ru/news/the-incident/584371-zaderzhali/":
            '<meta property="og:title" content="Задержали пятерых" /><h1>Задержали пятерых</h1>'
            '<p>29 октября 2026, 14:38</p><div class="js-image-description">В Ульяновской области задержали пятерых.'
            '<iframe src="https://rutube.ru/play/embed/6a0225b667142b1d7e131717ddede18d"></iframe></div>'
            '<div class="novelty__actions">Поделиться</div>'}
    monkeypatch.setattr(sources.av, "get", lambda u: pages[u])     # sources imports article_video by its own path
    got = sources.site("https://tnv.ru/news/the-incident/", cache=tmp_path)
    assert len(got) == 1                                                  # not the page list, not another section
    p = got[0]
    assert p["ch"] == "tnv.ru" and p["dt"] == "2026-10-29T11:38"          # Moscow 14:38 kept as UTC, like t.me
    assert p["text"] == "Задержали пятерых. В Ульяновской области задержали пятерых."
    assert p["mp4"] == "rutube:6a0225b667142b1d7e131717ddede18d" and p["official"] is False
    assert sources.channel(["rutube", "23350159"]) == "МВД России (Rutube)"


def test_an_agency_release_passes_the_war_filter_that_telegram_posts_do_not():
    from datetime import datetime, timezone

    from addons.news import pick

    now = datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)
    nak = {"key": "n", "dt": "2026-10-29T20:00", "channels": ["НАК (YouTube)"], "links": [], "media_got": ["mp4"],
           "text": "В Казани ФСБ задержала агента СБУ, готовившего теракт на объекте транспорта"}
    assert pick.judge({**nak, "official": True}, now, 36, set(), set(), "kazan")["skip"] == []
    assert "война/БПЛА/политика" in pick.judge(nak, now, 36, set(), set(), "kazan")["skip"]
    assert pick.judge({**nak, "official": True}, now, 36, set(), set(), "kazan")["official"] is True


def test_prostitution_with_a_minor_is_never_offered():
    from datetime import datetime, timezone

    from addons.news import pick

    now = datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)
    e = {"key": "x", "dt": "2026-10-29T13:00", "channels": ["tnv.ru"], "links": [], "media_got": [],
         "text": "В Татарстане раскрыли организацию и покровительство проституции с несовершеннолетними"}
    assert "сексуальное насилие" in pick.judge(e, now, 36, set(), set(), "kazan")["skip"]


def test_a_thesis_lasts_four_to_seven_seconds_whatever_the_cuts(tmp_path: Path):
    v = _clip(tmp_path / "v.mp4")
    long_clip = [{"path": v, "video": True, "start": 1.0, "dur": 12.0, "w": 0, "thesis": "*Трое* под стражей"}]
    assert build.theses(long_clip)[2] == [(1.0, 8.0)]       # a whole clip runs on, the thesis leaves after 7 s
    short = [{"path": v, "video": True, "start": 0.0, "dur": 1.8, "w": 0, "thesis": "Тезис"},
             {"path": v, "video": True, "start": 1.8, "dur": 1.6, "w": 0},
             {"path": v, "video": True, "start": 3.4, "dur": 2.0, "w": 0}]
    assert build.theses(short)[2] == [(0.0, 5.4)]           # whole cuts until 4 s have passed


def test_a_cold_open_starts_on_the_first_story_pictures():
    s = {"anchor": {"intro": "", "outro": "Это «ЧП Казань»."}, "cold_open": True,
         "stories": [{"lead": "Лидера ОПГ тайно привезли в Казань.", "text": "Его этапировали ночью.", "comment": "Ждём суда."},
                     {"lead": "Дальше.", "text": "Горит склад.", "comment": "Берегитесь."}]}
    words = [[w, 0.3 + i * 0.5, 0.3 + i * 0.5 + 0.4] for i, w in enumerate(build.full_text(s).split())]
    ws = build.story_windows(s, words, 12.0)
    assert ws[0][0] == 0.0 and ws[1][0] > 0.0                       # the hook line is read over the pictures
    assert build.story_windows({**s, "cold_open": False}, words, 12.0)[0][0] > 0.0


def test_a_story_becomes_its_own_looping_short(tmp_path: Path, monkeypatch):
    s = {"anchor": {"dir": "addons/news/anchor_kzn", "intro": "Привет.", "outro": "Пока."}, "headline": "Выпуск",
         "stories": [{"lead": "Начнём.", "text": "Горит театр на юге.", "comment": "Берегите себя.", "headline": "Театр"},
                     {"lead": "Дальше.", "text": "Ищут девушку.", "comment": "Звоните.", "headline": "Поиск"}]}
    words = [[w, round(0.3 + i * 0.5, 3), round(0.7 + i * 0.5, 3)] for i, w in enumerate(build.full_text(s).split())]
    timing = {"words": words, "duration": 9.0, "windows": build.story_windows(s, words, 9.0)}
    (tmp_path / "assets").mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "9",
                    str(tmp_path / "assets" / "narration.mp3")], check=True)
    v = _clip(tmp_path / "v.mp4", 6.0)
    w0, w1 = timing["windows"][0]
    items = [{"path": v, "video": True, "in": 0, "start": w0, "dur": round(w1 - w0, 3), "w": 0, "thesis": "*Театр*"},
             {"path": v, "video": True, "in": 0, "start": timing["windows"][1][0], "dur": 1.0, "w": 1}]
    got = {}
    monkeypatch.setattr(build, "broll", lambda p, its: got.setdefault("items", its))
    monkeypatch.setattr(build, "sound_bed", lambda *a: None)
    monkeypatch.setattr(build, "write_workspace", lambda p, story, t, its: got.update(story=story, t=t) or p)
    monkeypatch.setattr(build, "render", lambda ws, out: None)
    out = build.split_story(tmp_path, s, timing, items, 0, cat=True)
    assert out.name == "tiktok_1.mp4"
    its, t, one = got["items"], got["t"], got["story"]
    assert len(its) == 1 and its[0]["start"] == 0.0 and its[0]["thesis"] == "*Театр*"   # pictures from the first frame
    assert [w[0] for w in t["words"]] == ["Горит", "театр", "на", "юге.", "Берегите", "себя."]  # no greeting, no lead
    assert t["words"][0][1] == 0.55 and t["windows"] == [[0.0, round(w1 - (words[2][1] - 0.25 - 0.3), 3)]]
    assert one["loop"] and one["anchor"]["intro"] == "" and one["headline"] == "Театр" and len(one["stories"]) == 1
    secs = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(tmp_path / "tiktok" / "1" / "assets" / "narration.mp3")],
                                capture_output=True, text=True).stdout)
    assert abs(secs - (t["duration"] - 0.3)) < 0.15                 # the voice from the story's first word to the comment


def test_the_cat_keeps_his_head_in_every_look_and_the_looks_rotate_by_day(tmp_path: Path):
    import cv2

    from addons.news import anchor_looks as al

    cfg = {"mouth_box": [440, 700, 650, 880], "eyes_box": [405, 555, 695, 715]}
    m = al.head_mask(cfg)
    assert m[790, 545, 0] > 0.99 and m[620, 550, 0] > 0.99       # mouth and eyes come from the base, untouched
    assert m[100, 100, 0] < 0.01 and m[1800, 900, 0] < 0.01      # the window and the desk are the look's own
    d = tmp_path / "anchor"
    (d / "layers" / "looks").mkdir(parents=True)
    for f in ("layers/base.jpg", "layers/looks/rain.jpg", "layers/looks/snow.jpg"):
        cv2.imwrite(str(d / f), np.zeros((4, 4, 3), np.uint8))
    days = {al.pick(d, {}, day).name for day in range(3)}
    assert days == {"base.jpg", "rain.jpg", "snow.jpg"}
    assert al.pick(d, {"anchor": {"look": "snow"}}, 0).name == "snow.jpg"
    assert al.pick(d, {"anchor": {"look": "base"}}, 1).name == "base.jpg"



def test_a_short_without_the_cat_ends_on_the_story_last_word(tmp_path: Path, monkeypatch):
    s = {"anchor": {"dir": "addons/news/anchor_kzn", "intro": "Привет.", "outro": "Пока."}, "headline": "Выпуск",
         "stories": [{"lead": "Начнём.", "text": "Горит театр на юге.", "comment": "Берегите себя.", "headline": "Театр"},
                     {"lead": "Дальше.", "text": "Ищут девушку.", "comment": "Звоните.", "headline": "Поиск"}]}
    words = [[w, round(0.3 + i * 0.5, 3), round(0.7 + i * 0.5, 3)] for i, w in enumerate(build.full_text(s).split())]
    timing = {"words": words, "duration": 9.0, "windows": build.story_windows(s, words, 9.0)}
    (tmp_path / "assets").mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "9",
                    str(tmp_path / "assets" / "narration.mp3")], check=True)
    v = _clip(tmp_path / "v.mp4", 6.0)
    w0, w1 = timing["windows"][0]
    items = [{"path": v, "video": True, "in": 0, "start": w0, "dur": round(w1 - w0, 3), "w": 0}]
    got = {}
    monkeypatch.setattr(build, "broll", lambda p, its: got.setdefault("items", its))
    monkeypatch.setattr(build, "sound_bed", lambda *a: None)
    monkeypatch.setattr(build, "write_workspace", lambda p, story, t, its: got.update(story=story, t=t) or p)
    monkeypatch.setattr(build, "render", lambda ws, out: None)
    build.split_story(tmp_path, s, timing, items, 0)                    # the default: no cat
    t, one = got["t"], got["story"]
    assert [w[0] for w in t["words"]] == ["Горит", "театр", "на", "юге."] and one["anchor"] == {}
    assert t["windows"][0][1] == t["duration"] and got["items"][-1]["start"] + got["items"][-1]["dur"] == t["duration"]


def test_moscow_is_not_the_moscow_region_and_sexual_violence_is_caught_in_any_wording():
    from datetime import datetime, timezone

    from addons.news import pick

    now = datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)
    base = {"dt": "2026-10-29T12:00", "channels": ["mash"], "links": [], "media_got": []}
    region = pick.judge({**base, "key": "a", "text": "Житель Подмосковья ворвался в школу в Ивантеевке, задержан"},
                        now, 36, set(), set(), "moscow")
    oblast = pick.judge({**base, "key": "b", "text": "В Московской области задержан грабитель"}, now, 36, set(), set(), "moscow")
    city = pick.judge({**base, "key": "c", "text": "В Москве задержан грабитель, московский суд арестовал его"},
                      now, 36, set(), set(), "moscow")
    assert "не Москва" in region["skip"] and "не Москва" in oblast["skip"] and "не Москва" not in city["skip"]
    sexual = pick.judge({**base, "key": "d", "channels": ["moscowach"],
                         "text": "В Москве задержаны подозреваемые: один совершил насильственные действия сексуального характера"},
                        now, 36, set(), set(), "moscow")
    assert "сексуальное насилие" in sexual["skip"]


def test_a_thesis_gives_way_to_the_next_one(tmp_path: Path):
    v = _clip(tmp_path / "v.mp4")
    its = [{"path": v, "video": True, "start": 0.0, "dur": 2.0, "w": 0, "thesis": "Хук"},
           {"path": v, "video": True, "start": 2.0, "dur": 6.0, "w": 0, "thesis": "Факт"}]
    html, _, spans = build.theses(its)
    assert spans == [(0.0, 2.0), (2.0, 8.0)] and 'data-duration="2.0"' in html[0]


def test_stills_take_turns_panning_left_to_right_and_pushing_in(tmp_path: Path):
    assert [build.still_move({"n": n}) for n in range(4)] == ["pan", "zoom", "pan", "zoom"]
    assert build.still_move({"n": 0, "move": "zoom"}) == "zoom" and build.still_move({"n": 1, "zoom": [1.07, 1.0]}) == "zoom"
    png = tmp_path / "p.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360", "-frames:v", "1",
                    str(png)], check=True)
    out = tmp_path / "pan.mp4"
    build.segment({"path": png, "dur": 1.0, "video": False, "n": 0}, out)
    import cv2

    cap = cv2.VideoCapture(str(out))
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)[build.BOX_Y - 100:build.BOX_Y + 100].astype(np.float32))
    a, b = frames[0], frames[-1]
    shift = cv2.phaseCorrelate(a, b)[0][0]
    assert shift < -20                                       # the picture slides left: the camera goes right


def test_a_one_story_film_opens_on_its_hook_and_the_plate_is_the_brand(tmp_path: Path):
    s = {"slug": "x", "tag": "ЧП · КАЗАНЬ", "date": "29 октября", "headline": "Гормон в багаже",
         "hook": "Иностранец вёз в багаже *20 флаконов гормона роста*", "loop": True,
         "narration": {"text": "Иностранец вёз флаконы гормона."}}
    words = [[w, round(0.3 + i * 0.4, 3), round(0.6 + i * 0.4, 3)] for i, w in enumerate(build.full_text(s).split())]
    assert build.story_windows(s, words, 3.0) == [[0.0, 3.0]]           # pictures from the first frame, no cat
    html, js = build.hook_html(s, 3.0)
    assert 'data-start="0" data-duration="3.0"' in html and js == ""     # the title card for the whole film, static
    assert "<em>20 флаконов гормона роста</em>" in html and "ЧП · КАЗАНЬ" in html and "29 октября" in html
    assert build.hook_html({**s, "hook": ""}, 3.0) == ("", "")


def test_the_library_keeps_real_pictures_by_tags(tmp_path: Path):
    from addons.news import library as lib

    pic = tmp_path / "Kazan_Airport_Terminal_1A.jpg"
    pic.write_bytes(b"jpeg-bytes")
    e = lib.add(pic, ["Казань", "аэропорт", "терминал"], "Фото: X / Wikimedia, CC BY-SA 3.0", "CC BY-SA 3.0", "https://c", tmp_path / "lib")
    again = lib.add(pic, ["Кремлёвская"], r=tmp_path / "lib")
    assert again["id"] == e["id"] and "кремлевская" in again["tags"]          # one file, the tags merged
    other = tmp_path / "court.jpg"
    other.write_bytes(b"other")
    lib.add(other, ["Казань", "суд"], r=tmp_path / "lib")
    found = lib.find(["казань", "аэропорт"], tmp_path / "lib")
    assert [x["name"] for x in found] == ["Kazan_Airport_Terminal_1A.jpg", "court.jpg"]   # both words first
    out = lib.copy(e["id"], tmp_path / "media", tmp_path / "lib")
    assert out.read_bytes() == b"jpeg-bytes"


def test_no_ai_pictures_anywhere():
    cities = json.loads((Path(build.__file__).parent / "cities.json").read_text(encoding="utf-8"))
    assert all(c.get("ai_max_share") == 0 for c in cities.values() if isinstance(c, dict))
    story = {"city": "kazan", "shots": [{"src": "illustrations/k1.png", "label": "ИИ-иллюстрация"}, {"src": "media/a.jpg"}]}
    ai, total, limit = build.ai_budget(story)
    assert (ai, total, limit) == (1, 2, 0)


def test_a_one_story_film_has_its_footage_cleaned(tmp_path: Path):
    from addons.news import clean_footage as cf

    story = {"narration": {"text": "x"}, "shots": [{"src": "media/sk.mp4", "in": 6}, {"src": "media/nak.mp4", "clean": "none"}]}
    assert list(cf.footage(tmp_path, story)) == ["media/sk.mp4@6"]


def test_a_sound_bite_sits_between_two_takes_and_gets_its_own_slot(tmp_path: Path, monkeypatch):
    story = {"narration": {"engine": "fishaudio", "text": "Его задержали. [bite] Ему грозит до пятнадцати лет."},
             "bite": {"src": "media/sk.mp4", "in": 1.0, "out": 3.0},
             "shots": [{"src": "media/a.mp4", "beat": "Его задержали"}, {"src": "media/sk.mp4", "in": 1.0, "bite": True},
                       {"src": "media/b.mp4", "beat": "Ему грозит до"}]}
    (tmp_path / "media").mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=5", "-f", "lavfi", "-i",
                    "testsrc=size=160x90:d=5", "-shortest", str(tmp_path / "media" / "sk.mp4")], check=True)

    def fake_speak(n, out):  # a take as long as its words, 0.4 s a word
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t",
                        str(0.4 * len(n["text"].split())), str(out)], check=True)
    monkeypatch.setattr(build, "speak", fake_speak)
    A = tmp_path / "assets"
    A.mkdir()
    build.sound_bite(tmp_path, story, A)
    span = json.loads((A / "bite.json").read_text())["span"]
    assert abs(span[0] - (0.8 + build.BITE_GAP)) < 0.06 and abs(span[1] - span[0] - 2.0) < 0.01
    words = [["Его", 0.3, 0.6], ["задержали.", 0.6, 1.1], ["Ему", 3.6, 3.9], ["грозит", 3.9, 4.2], ["до", 4.2, 4.3],
             ["пятнадцати", 4.3, 4.8], ["лет.", 4.8, 5.1]]
    timing = {"words": words, "duration": 6.0, "windows": [[0.0, 6.0]], "bite": [1.35, 3.35]}
    ph = build.story_beats(story, timing)[0]
    assert [p["text"] for p in ph] == ["Его задержали.", "[прямая речь]", "Ему грозит до пятнадцати лет."]
    assert ph[1]["start"] == 1.35 and ph[2]["start"] >= 3.35


def test_a_piece_never_opens_on_the_last_frames_of_the_scene_before(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(build, "scene_cuts", lambda src: [6.48, 11.76])
    v = _clip(tmp_path / "v.mp4", 20.0)
    story = {"shots": [{"src": "v.mp4", "in": 6.0}]}
    item = build.slots(story, [0.0, 4.0], tmp_path)[0]
    assert item["in"] == 6.52                                   # past the cut at 6.48, not the anchor's last frames


def test_a_one_story_film_never_crops_a_picture(tmp_path: Path):
    def clip(name, size):
        f = tmp_path / f"{name}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration=1",
                        "-pix_fmt", "yuv420p", str(f)], check=True)
        return f
    wide, square, tall, phone = clip("wide", "1280x720"), clip("square", "640x640"), clip("tall", "976x1266"), clip("phone", "720x1280")
    assert build.frame_box({"path": wide, "fit": True}) == (1080, 760, 0, 550, True)      # 16:9: the box, sides trimmed
    assert build.frame_box({"path": square, "fit": True}) == (1080, 1080, 0, 520, False)  # whole, no side margins
    assert build.frame_box({"path": tall, "fit": True}) == (1080, 1920, 0, 0, True)       # a vertical video: full screen
    assert build.frame_box({"path": phone, "fit": True}) == (1080, 1920, 0, 0, True)      # «на весь экран» (31.10)
    from PIL import Image
    for name, size in (("tall_photo", (976, 1266)), ("phone_photo", (720, 1280))):
        Image.new("RGB", size, "gray").save(tmp_path / f"{name}.jpg")
    assert build.frame_box({"path": tmp_path / "tall_photo.jpg", "fit": True}) == (1078, 1400, 1, 520, False)  # whole
    assert build.frame_box({"path": tmp_path / "phone_photo.jpg", "fit": True}) == (1080, 1920, 0, 0, True)    # nothing lost
    assert build.frame_box({"path": wide})[4] is True                                    # digests: the old box
    out = tmp_path / "s.mp4"
    build.segment({"path": phone, "dur": 0.5, "video": True, "in": 0.0, "fit": True}, out)
    assert abs(build.probe(out)[2] - 0.5) < 0.1


def test_youtube_captions_give_the_moment_the_words_are_said():
    from addons.news import yt_footage as yt

    json3 = {"events": [{"tStartMs": 0, "segs": [{"utf8": "Сегодня"}, {"utf8": " в", "tOffsetMs": 400}]},
                        {"tStartMs": 12000, "segs": [{"utf8": "на перекрёстке"}, {"utf8": " проехала", "tOffsetMs": 900},
                                                     {"utf8": " на красный", "tOffsetMs": 1500}]},
                        {"tStartMs": 40000, "segs": [{"utf8": "красный"}, {"utf8": " свет", "tOffsetMs": 300}]}]}
    words = yt.words_of(json3)
    assert words[2] == ("на", 12.0) and words[4] == ("проехала", 12.9)
    m = yt.moments(words, "перекрёсток красный проехала")
    assert m[0]["from"] == 11.0 and m[0]["score"] == 3 and "перекрёстке" in m[0]["text"]   # the best place first
    assert yt.url_of("CgMpcYcJqZM") == "https://www.youtube.com/watch?v=CgMpcYcJqZM"


def test_every_text_keeps_the_safe_zone(tmp_path: Path):
    assert f"padding: {build.SAFE_TOP}px {build.SAFE_X}px" in build.HOOK_CSS          # the hook under the app's top
    assert f"padding: 240px {build.SAFE_X}px" in build.THESIS_CSS                       # theses off the cropped sides
    clip = tmp_path / "phone.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30:duration=1",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    assert build.thesis_bottom({"path": clip, "fit": True}) <= build.SAFE_BOTTOM - build.CAP_ROOM  # above the captions


def test_world_finds_the_story_abroad_and_leaves_out_the_war():
    from addons.news import world

    def post(ch, n, views, text):
        return {"channel": ch, "post": n, "date": f"2026-10-30T20:{n:02d}:00+00:00", "views": views, "media": "video",
                "album": [n], "ref": f"tg:{ch}/{n}", "text": text}

    posts = [post("toporlive", 1, 926134, "В Польше происходит государственный переворот, — Конституционный трибунал. "
                                          "Правительство Туска воспользовалось отсутствием президента Навроцкого"),
             post("mash", 2, 263263, "Госпереворот в Польше, — Муларчик. Президент Навроцкий возвращается в Варшаву, "
                                     "Конституционный трибунал оцеплен"),
             post("rian_ru", 3, 146540, "Толпа окружила Конституционный трибунал Польши, полиция заняла оборону"),
             post("bbbreaking", 4, 210724, "Польша передаст Украине два истребителя МиГ-29"),              # the war
             post("tass_agency", 5, 9780, "Дмитриев прокомментировал протесты в Польше"),                  # Moscow's comment
             post("shot_shot", 6, 50000, "В Таиланде туристку укусила обезьяна")]                         # one channel only
    found = world.stories(posts)
    assert [s["country"] for s in found] == ["pl"]
    assert [p["ref"] for p in found[0]["posts"]] == ["tg:toporlive/1", "tg:mash/2", "tg:rian_ru/3"]
    assert found[0]["reach"] == round((926134 + 263263 + 146540) * 2.0)
    assert any(s.startswith("Навроцк") for s in found[0]["stems"])


def test_a_post_screenshot_becomes_a_card_that_keeps_its_text(tmp_path: Path):
    from PIL import Image

    from addons.news import build, dossier

    shot = tmp_path / "post.jpg"
    Image.new("RGB", (596, 172), "white").save(shot)
    out = dossier.card(shot, tmp_path / "card.jpg")
    im = Image.open(out)
    assert im.size == (1080, 760) and im.getpixel((5, 5))[0] > 240                        # its own background
    assert dossier.CARD_TEXT_W <= 1080 / build.PAN_Z                                      # a pan never cuts the text
    w, h, x, y, cover = build.frame_box({"path": out, "fit": True})
    assert (w, h) == (1080, 760) and cover                                                # the box shows it whole


def test_a_staged_youtube_video_is_never_footage():
    from addons.news import yt_footage as yt

    assert yt.STAGED.search("Powrót Samolotu… Inscenizacja X-Plane 12 by Artanis")
    assert yt.STAGED.search("POLISH PRESIDENT'S PLANE U-TURN — AI animation")
    assert not yt.STAGED.search("Szarpanina w TK z udziałem Nawrockiego. Berek: prezydent wszedł")
    assert not yt.STAGED.search("Lindy 3.0: Build AI Agents with Just Text Prompts")       # a story about AI
    assert yt.STAGED.search("Prezydent w TK — film wygenerowany, AI-generated")


def test_a_world_story_may_hold_four_theses():
    shots = [{"src": "a.mp4", "thesis": f"*{i}* тезис"} for i in range(4)]
    assert not [w for w in build.thesis_warnings({"kind": "world", "shots": shots}) if "theses" in w]
    assert [w for w in build.thesis_warnings({"shots": shots}) if "theses" in w]


def test_a_thesis_over_a_picture_that_goes_on_below_fades_out(tmp_path: Path):
    from PIL import Image

    wide, tall = tmp_path / "wide.jpg", tmp_path / "tall.jpg"
    Image.new("RGB", (1280, 720), "gray").save(wide)
    Image.new("RGB", (960, 1280), "gray").save(tall)          # shown whole from y=520 down to the bottom
    assert not build.thesis_over({"path": wide, "fit": True})  # the band ends on the picture's own edge
    assert build.thesis_over({"path": tall, "fit": True})      # «тень выглядит оторванной» (31.10)
    items = [{"path": tall, "fit": True, "start": 0.0, "dur": 5.0, "thesis": "*Награда* от МЧС", "w": 0}]
    html, _, _ = build.theses(items, not_before=0.0)
    assert 'class="clip thesis over"' in html[0] and ".thesis.over" in build.THESIS_CSS


def test_a_piece_whose_scene_repeats_is_flagged(tmp_path: Path):
    from addons.news import yt_footage as yt

    clip = tmp_path / "alike.mp4"   # a take, another one, then the first again: «4 videos» of alike takes (01.11)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2",
                    "-f", "lavfi", "-i", "color=c=red:size=320x240:rate=25:duration=2",
                    "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2",
                    "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1[v]", "-map", "[v]", "-pix_fmt", "yuv420p", str(clip)],
                   check=True)
    cuts = yt.scenes(clip)
    assert [round(c) for c in cuts] == [2, 4]
    assert [(round(a), round(b)) for a, b in yt.repeats(clip, cuts)] == [(0, 4)]


def test_a_hand_list_over_a_found_mask_is_called_out():
    from addons.news import clean_footage as cf

    assert "⚠" in cf.replaced_note([], 0.012, 2.1)           # a drone clip marked «[]» that had a watermark
    assert cf.replaced_note({"add": []}, 0.012, 2.1) == ""     # the auto mask is kept
    assert cf.replaced_note([], 0.012, 0.2) == ""              # a still camera: its auto mask is not trusted anyway


def test_a_4k_phone_clip_goes_to_the_cleaner_at_most_1920_on_the_long_side(tmp_path: Path):
    """2160x3840 from Baza ran the cleaner's worker out of memory and hung the server (Kitay-gorod, 01.11)."""
    from addons.news import clean_footage as cf
    big, small = tmp_path / "big.mp4", tmp_path / "small.mp4"
    for f, size in ((big, "1000x2400"), (small, "1280x720")):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration=0.4",
                        "-c:v", "libx264", str(f)], check=True)
    for f, want in ((big, "800x1920"), (small, "1280x720")):
        out = tmp_path / f"{f.stem}.in.mp4"
        cf.prepare(f, 0.0, 0.3, out)
        got = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                              "-of", "csv=p=0:s=x", str(out)], capture_output=True, text=True).stdout.strip()
        assert got == want


# -- viral pick for TikTok (owner 2026-11-01) ---------------------------------------------------------------------------
def _vpost(ch, n, views, text, age_h, now, **kw):
    return {"channel": ch, "post": n, "ts": now - age_h * 3600, "views": views, "forwards": 0, "replies": 0,
            "reactions": 0, "text": text, "video_s": 0.0, "photos": 0, "group": 0, "fwd": False,
            "link": f"https://t.me/{ch}/{n}", "ref": f"tg:{ch}/{n}", "src": "tg", **kw}


def _filler(n):
    """Posts of unrelated made-up words: the corpus that makes an event's shared words rare, as in a day's feed."""
    abc = "абвгдежзиклмнопрстуфхцчшщэюя"
    words = lambda i, j: "".join(abc[(i * 7 + j * 3 + k * 5) % len(abc)] for k in range(7))  # noqa: E731
    return [" ".join(f"{words(i, j)}" for j in range(8)) + " событие обычное" for i in range(n)]


def test_viral_boundaries_keep_the_war_and_russian_politics_out_but_protests_abroad_and_laws_in():
    from addons.news import viral

    assert "война/СВО" in viral.boundaries("ВСУ атаковали Белгород беспилотниками")
    assert "протесты в России" in viral.boundaries("В Москве прошёл митинг против повышения тарифов ЖКХ")
    assert viral.boundaries("В Германии тысячи зумеров вышли на протест против военного бюджета") == []
    assert viral.boundaries("Госдума приняла закон о штрафах за громкую музыку в квартирах") == []
    assert "заявления власти" in viral.boundaries("Песков заявил, что цены на бензин стабильны")
    assert "политика РФ" in viral.boundaries("Кандидаты в губернаторы начали предвыборную кампанию, оппозиция против")
    assert viral.boundaries("Выборы в Чехии: оппозиция Бабиша победила") == []     # another country's politics
    # world politics is in (owner 2026-11-02), the war of Russia and Ukraine stays out
    assert viral.boundaries("Нетаньяху потребовал от Трампа возобновить удары по Ирану после выборов") == []
    assert "война/СВО" in viral.boundaries("Трамп обсудил с Зеленским поставки ракет")
    assert "война/СВО" in viral.boundaries("Ночью над областью сбили беспилотники, повреждён дом")
    assert "политика РФ" in viral.boundaries("Суд признал иноагентом блогера")
    assert "дети-жертвы" in viral.boundaries("Школьник погиб, упав с крыши гаража")
    assert "реклама" in viral.boundaries("Розыгрыш iPhone среди подписчиков, промокод внутри")


def test_viral_velocity_is_against_the_channel_usual_at_that_age():
    from addons.news import viral

    now = 1_800_000_000.0
    usual = [_vpost("mash", i, int(100_000 * viral.curve(a)), "обычный пост", a, now) for i, a in
             enumerate([2, 3, 4, 5, 6, 8, 10, 12, 16, 20], 1)]
    hot = _vpost("mash", 99, int(4 * 100_000 * viral.curve(1.0)), "горячий пост", 1.0, now)
    norms = viral.channel_norms(usual + [hot], now)
    assert abs(viral.velocity(hot, norms, now) - 4.0) < 0.2
    assert viral.velocity(usual[5], norms, now) < 1.5
    sig = viral.signals({"posts": [hot]}, norms, now)
    feats = viral.signal_features(sig)
    assert 0.8 < feats["velocity"] <= 1.0 and feats["pickup"] == 0.0
    early = dict(usual[0], early=(0.5, int(5 * 100_000 * viral.curve(0.5))))  # the engine saw it at 30 min
    assert viral.velocity(early, norms, now) >= 4.5


def test_viral_score_weighs_the_topic_the_signals_and_fades_with_age():
    from addons.news import viral

    w = viral.WEIGHTS
    assert abs(sum(w.values()) - 100) < 1e-6
    full = {k: 10 for k in viral.AI_FEATURES}
    sigf = {k: 1.0 for k in viral.SIGNALS}
    assert viral.score(full, sigf, 0, w) == 100.0
    dull = dict(full, everyone=2)
    assert viral.score(dull, sigf, 0, w) < viral.score(full, sigf, 0, w)
    assert viral.score(full, sigf, 24, w) == 85.0                     # a day old: × 0.85 (owner 2026-11-02)
    assert viral.score(full, sigf, 72, w) == 55.0                     # three days: × 0.55, still in
    assert viral.score(full, sigf, 0, w, regional=True) == 85.0       # a city story with a common hook


def test_viral_groups_one_event_across_channels_and_skips_what_is_made_or_claimed():
    from addons.news import viral

    now = 1_800_000_000.0
    posts = [_vpost("feed", i, 1000, t, 5, now) for i, t in enumerate(_filler(120), 1)]
    ev = ["В Берлине 40 тысяч зумеров вышли против военного бюджета Мерца в 108 миллиардов евро",
          "Зумеры Берлина протестуют: бюджет Мерца на армию 108 миллиардов евро, на улицах 40 тысяч",
          "Протест зумеров в Берлине против 108 миллиардов евро на армию — правительство Мерца молчит"]
    posts += [_vpost(ch, 500 + i, 300_000, t, 3 + i, now) for i, (ch, t) in enumerate(zip(["mash", "topor", "shot_shot"], ev))]
    posts.append(_vpost("kazan", 900, 20_000, "В Казани открыли новый мост через Казанку для пешеходов и велосипедов", 2, now))
    ranked = viral.rank(posts, now, viral.WEIGHTS, [], [], set(), set(), use_ai=False)
    berlin = next(s for s in ranked if s["seed"]["channel"] in ("mash", "topor", "shot_shot"))
    assert sorted(p["channel"] for p in berlin["posts"]) == ["mash", "shot_shot", "topor"]
    assert berlin["region"] == "world" and berlin["place"] == "Германия" and not berlin["skip"]
    assert berlin["signals"]["channels"] == 3 and berlin["signals"]["channels_3h"] == 3
    # made: by a link, by the key, by a film of the same event; claimed by the other account's film
    made = [{"film": "news-de-01-11-1", "text": ev[0], "key": None, "links": []}]
    again = viral.rank(posts, now, viral.WEIGHTS, made, [], set(), set(), use_ai=False)
    assert any("уже сделано" in r for s in again for r in s["skip"] if s["key"] == berlin["key"])
    claims = [{"film": "news-viral-b-01-11-1", "key": "x", "links": ["https://t.me/topor/501"]}]
    taken = viral.rank(posts, now, viral.WEIGHTS, [], claims, set(), set(), use_ai=False)
    assert any("занято: news-viral-b-01-11-1" in r for s in taken for r in s["skip"] if s["key"] == berlin["key"])


def test_viral_ai_flags_and_a_local_story_without_a_common_hook_are_dropped(monkeypatch):
    from addons.news import viral

    now = 1_800_000_000.0
    posts = [_vpost("feed", i, 1000, t, 5, now) for i, t in enumerate(_filler(110), 1)]
    posts += [_vpost("moscowach", 1, 50_000, "На Тверской подрались двое водителей самокатов, полиция разняла", 2, now),
              _vpost("mash", 2, 400_000, "Цены на гречку выросли на 30 процентов за месяц: покупатели возмущены", 2, now),
              _vpost("topor", 3, 300_000, "Учителям в школах Подмосковья урезали надбавки за классное руководство", 2, now)]

    def fake_ai(stories, batch=8, workers=3):
        for st in stories:
            t = st["seed"]["text"]
            st["ai"] = {"everyone": 9, "emotion": 8, "debate": 8, "visual": 5, "explain": 8, "war": False,
                        "ru_politics": False, "risky": False, "ad": False, "local_only": "Тверской" in t,
                        "common_hook": None, "topic": "цены", "ai": True}
            if "Учителям" in t:
                st["ai"].update(ru_politics=True)
        return len(stories)

    monkeypatch.setattr(viral, "ai_score", fake_ai)
    ranked = viral.rank(posts, now, viral.WEIGHTS, [], [], set(), set(), ai=200)
    by = {s["seed"]["channel"]: s for s in ranked if s["seed"]["channel"] in ("moscowach", "mash", "topor")}
    assert by["moscowach"]["skip"] == ["только местное — в Telegram-канал города"]
    assert by["topor"]["skip"] == ["ИИ: политика РФ"]
    assert not by["mash"]["skip"] and ranked[0] is by["mash"]



def test_viral_the_ai_shortlists_every_story_and_its_score_is_final(monkeypatch):
    """Owner 2026-11-02: «отбор новостей, их оценку и выходной балл — ИИ, а не алгоритмам»."""
    from addons.news import viral

    now = 1_800_000_000.0
    posts = [_vpost("feed", i, 1000, t, 5, now) for i, t in enumerate(_filler(110), 1)]
    posts += [_vpost("mash", 1, 400_000, "Цены на гречку выросли на 30 процентов за месяц: покупатели возмущены", 2, now),
              _vpost("topor", 2, 2_000, "Нетаньяху потребовал от Трампа возобновить удары по Ирану после выборов в США", 40, now),
              _vpost("shot_shot", 3, 300_000, "ВСУ атаковали беспилотниками Белгородскую область ночью, повреждены дома", 2, now)]
    seen = {}

    def fake_triage(stories, batch=80, workers=3):
        for st in stories:
            t = st["seed"]["text"]
            st["triage"] = 90.0 if "Нетаньяху" in t else 60.0 if "гречку" in t else 0.0
        seen["n"] = len(stories)
        return len(stories)

    def fake_ai(stories, batch=8, workers=3):
        seen["deep"] = [st["seed"]["channel"] for st in stories]
        for st in stories:
            t = st["seed"]["text"]
            st["ai"] = {"everyone": 5, "emotion": 5, "debate": 5, "visual": 5, "explain": 8, "war": False,
                        "ru_politics": False, "risky": False, "ad": False, "local_only": False, "common_hook": None,
                        "topic": "мир и общество", "ai": True, "score": 88.0 if "Нетаньяху" in t else 61.0}
        return len(stories)

    monkeypatch.setattr(viral, "ai_triage", fake_triage)
    monkeypatch.setattr(viral, "ai_score", fake_ai)
    ranked = viral.rank(posts, now, viral.WEIGHTS, [], [], set(), set(), ai=2)
    by = {s["seed"]["channel"]: s for s in ranked if s["seed"]["channel"] in ("mash", "topor", "shot_shot")}
    # the AI looked at every story, war words included; its top two went to the deep pass
    assert seen["n"] > 100 and seen["deep"][0] == "topor" and "mash" in seen["deep"]
    # world politics with war words abroad: the AI set no flag — in, and first by the AI's score although it is
    # 40 h old with few views (its formula is far lower)
    assert not by["topor"]["skip"] and ranked[0] is by["topor"]
    assert by["topor"]["score"] == 88.0 and by["topor"]["formula"] != 88.0
    # the war of Russia and Ukraine: the filters hinted, the AI never cleared it — out
    assert by["shot_shot"]["hints"] and any("ИИ не проверял" in r for r in by["shot_shot"]["skip"])
    # without the AI the filters cut as before: the war of Russia and Ukraine out, world politics in
    plain_ = viral.rank(posts, now, viral.WEIGHTS, [], [], set(), set(), use_ai=False)
    assert not any(s["seed"]["channel"] == "shot_shot" for s in plain_)
    assert any(s["seed"]["channel"] == "topor" and not s["skip"] for s in plain_)

def test_viral_slots_share_the_best_stories_out_in_turn_without_repeats():
    from datetime import date
    from addons.news import viral

    accs = [{"key": "a", "name": "account_a"}, {"key": "b", "name": "account_b"}]
    d1, d2 = date(2026, 11, 2), date(2026, 11, 3)
    order = [viral.slot_account(s, d1, accs)["key"] for s in (1, 2, 3, 4)]
    assert order in (["a", "b", "a", "b"], ["b", "a", "b", "a"])         # 1, 3 one account's; 2, 4 the other's
    assert viral.slot_account(1, d1, accs)["key"] != viral.slot_account(1, d2, accs)["key"]  # who goes first alternates
    assert [viral.film_number(s) for s in (1, 2, 3, 4)] == [1, 1, 2, 2]

    def st(key, sc, topic, region="russia", place="Россия"):
        return {"key": key, "score": sc, "ai": {"topic": topic}, "region": region, "place": place, "skip": []}

    stories = [st("s1", 95, "цены"), st("s2", 90, "цены"), st("s3", 85, "цены"), st("s4", 80, "жильё"),
               st("s5", 70, "медицина"), st("s6", 60, "мир и общество", "world", "Германия")]
    plan = viral.plan_day(stories, d1, 4, accs)
    first, second = viral.slot_account(1, d1, accs)["key"], viral.slot_account(2, d1, accs)["key"]
    assert [(slot, acc["key"], s["key"]) for slot, acc, s in plan] == [
        (1, first, "s1"), (2, second, "s2"), (3, first, "s4"), (4, second, "s5")]  # s3: «цены» again for both
    assert len({s["key"] for _, _, s in plan}) == 4                     # no story twice
    assert viral.same_kind(st("x", 1, "мир и общество", "world", "Германия"), stories[5])  # one country abroad
    assert viral.fit(st("x", 1, "цены"), {"avoid_topics": ["цены"]}) == 0.0


def test_viral_stats_take_the_owner_numbers_and_tune_the_weights_with_a_log(tmp_path: Path, monkeypatch):
    import random
    from addons.news import viral_stats as vs

    monkeypatch.setattr(vs.viral, "DATA", tmp_path / "data")
    con = vs.db(tmp_path / "data" / "stats.db")
    assert vs.parse(["15,4к", "41%", "230", "96"]) == {"views": 15400, "completion": 0.41, "comments": 230,
                                                        "shares": 96, "likes": None}
    assert vs.number("1.2м") == 1_200_000
    rng = random.Random(7)
    for i in range(1, 9):
        proj = tmp_path / "projects" / f"news-viral-a-0{i}-11-1"
        proj.mkdir(parents=True)
        debate = rng.randint(2, 10)
        (proj / "story.json").write_text(json.dumps({"headline": f"film {i}", "viral": {"account": "a", "key": f"k{i}"},
                                                     "source": {"key": f"k{i}", "links": []}}), encoding="utf-8")
        feats = {k: 5 for k in vs.viral.AI_FEATURES} | {"debate": debate}
        (proj / "viral.json").write_text(json.dumps({"account": "a", "candidates": [
            {"key": f"k{i}", "links": [], "score": 50, "features": feats,
             "signal_features": {k: 0.5 for k in vs.viral.SIGNALS}}]}), encoding="utf-8")
        views = int(2000 * 2 ** debate)                                   # the debate drives the views here
        if i <= 5:
            reply = vs.handle(f"/tt a-0{i}-11-1 {views} 40% 100 50", con, root=tmp_path)
            assert reply.startswith("✅ a-0")
    assert "нужно хотя бы" in vs.tune(con)                               # 5 films: nothing changes yet
    assert not (tmp_path / "data" / "weights.json").exists()
    for i in range(6, 9):
        views = int(2000 * 2 ** json.loads((tmp_path / "projects" / f"news-viral-a-0{i}-11-1" / "viral.json")
                                           .read_text())["candidates"][0]["features"]["debate"])
        vs.handle(f"/tt news-viral-a-0{i}-11-1 {views} 0.4 100 50", con, root=tmp_path)
    text = vs.tune(con)
    w = json.loads((tmp_path / "data" / "weights.json").read_text())["weights"]
    assert abs(sum(w.values()) - 100) < 0.05
    assert w["debate"] > vs.viral.WEIGHTS["debate"] and "спор" in text and "↑" in text
    for slug in ("news-viral-a-02-11-1", "news-viral-b-09-11-2"):           # delivered: with and without numbers
        (tmp_path / "projects" / slug).mkdir(parents=True, exist_ok=True)
        (tmp_path / "projects" / slug / "kinescope.json").write_text("{}", encoding="utf-8")
    assert vs.waiting(con, tmp_path) == ["b-09-11-2 (ещё нет суток)"]
    assert "Не нашёл" in vs.handle("/tt z-01-01-9 100 10 1 1", con, root=tmp_path)


def test_viral_one_event_told_two_ways_is_folded_by_meaning_and_a_taken_one_is_skipped(monkeypatch):
    """«Рейв в Ленинке» came as two stories the words did not join (01.11): the AI's same-event pass folds them, and
    a story about a film already taken today is skipped."""
    import types
    from addons.news import viral

    now = 1_800_000_000.0
    real = viral._channels_mod
    fake = types.SimpleNamespace(chat_json=lambda *a, **k: [{"id": 2, "same_as": 1}, {"id": 3, "same_as": "T1"}])
    monkeypatch.setattr(viral, "_channels_mod", lambda name: fake if name == "llm" else real(name))
    monkeypatch.setenv("CODEX_GW_SECRET", "x")

    def story(key, sc, ch, n):
        st = {"key": key, "score": sc, "skip": [], "region": "moscow", "place": "Москва", "h": {}, "note": None,
              "ai": {k: 7 for k in viral.AI_FEATURES} | {"hook": key}, "posts": [_vpost(ch, n, 100_000, key, 3, now)]}
        st["seed"] = dict(st["posts"][0], plain=key)
        st["signals"] = viral.signals(st, {}, now)
        st["sigf"] = viral.signal_features(st["signals"])
        return st

    top = [story("рейв в библиотеке", 50, "moscowach", 1), story("Ленинка объясняет рейв", 45, "ostorozhno_moskva", 2),
           story("воздухан в словарях", 40, "topor", 3)]
    claims = [{"film": "news-viral-a-01-11-1", "title": "«Воздухан» могут внести в словари"}]
    assert viral.same_events(top, [], claims, {}, now, viral.WEIGHTS) == 2
    assert top[1]["skip"] == ["тот же сюжет, что рейв в библиотеке"]
    assert top[0]["signals"]["channels"] == 2 and not top[0]["skip"]
    assert top[2]["skip"][0].startswith("занято по смыслу")


def test_one_heavy_job_at_a_time_knows_a_render_and_a_cleaning_from_light_steps():
    """Owner 2026-11-01: two films cleaning and rendering at once took the server down — produce.py and the viral
    slots wait for another film's render (build.py), cleaning (clean_footage.py --run) or produce.py."""
    from addons.news import produce

    heavy = ["python addons/news/build.py projects/x --retime", "python addons/news/build.py projects/x",
             "python addons/news/clean_footage.py projects/x --run", "python addons/news/produce.py projects/x --send"]
    light = ["python addons/news/build.py projects/x --voice-only", "python addons/news/build.py projects/x --storyboard",
             "python addons/news/clean_footage.py projects/x --plan", "python addons/news/viral.py pick --slot 1"]
    assert all(produce.HEAVY.search(a) for a in heavy) and not any(produce.HEAVY.search(a) for a in light)
    assert not produce.DIRECT.search(heavy[3])  # produce.py itself waits on the lock, not on the process list


def test_a_viral_slot_waits_for_a_film_another_agent_is_making(tmp_path: Path, monkeypatch):
    import os
    import time
    from addons.news import morning

    monkeypatch.setattr(morning, "REPO", tmp_path)
    for name, age_min, delivered in (("news-de-01-11-1", 2, False), ("news-msk-01-11-1", 2, True),
                                     ("news-pl-31-10-1", 90, False), ("news-viral-a-02-11-1", 1, False)):
        d = tmp_path / "projects" / name
        d.mkdir(parents=True)
        (d / "story.json").write_text("{}", encoding="utf-8")
        t = time.time() - age_min * 60
        os.utime(d / "story.json", (t, t))
        if delivered:
            (d / "kinescope.json").write_text("{}", encoding="utf-8")
    assert morning.films_in_work({"news-viral-a-02-11-1"}) == ["news-de-01-11-1"]  # fresh, not out yet


def test_viral_freshness_keeps_new_first_but_lets_an_older_story_win():
    from addons.news import viral

    assert viral.freshness(0) == 1
    assert abs(viral.freshness(24) - 0.85) < 1e-9 and abs(viral.freshness(72) - 0.55) < 1e-9
    assert viral.freshness(100) == viral.freshness(72)


def test_viral_the_ai_sees_our_real_tiktok_numbers(tmp_path, monkeypatch):
    import sqlite3

    from addons.news import viral

    monkeypatch.setattr(viral, "DATA", tmp_path)
    block = viral.our_results()
    assert "114 200 — Протесты в Германии" in block and block.index("114 200") < block.index("741")
    con = sqlite3.connect(tmp_path / "stats.db")
    con.executescript("CREATE TABLE films(slug TEXT, account TEXT, title TEXT); CREATE TABLE stats(slug TEXT, views INT);"
                      "INSERT INTO films VALUES('news-viral-a-03-11-1', 'a', 'Тест: залетел'); "
                      "INSERT INTO stats VALUES('news-viral-a-03-11-1', 250000);")
    con.commit(); con.close()
    first = [ln for ln in viral.our_results().split("\n") if ln.startswith("- ")][0]
    assert first == "- 250 000 — Тест: залетел (аккаунт a)"


GOOGLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel><title>Daily Search Trends</title>
<item><title>{q}</title><ht:approx_traffic>{t}</ht:approx_traffic><pubDate>Mon, 2 Nov 2026 11:00:00 -0800</pubDate>
<ht:picture>https://x/p.jpg</ht:picture>
<ht:news_item><ht:news_item_title>{n}</ht:news_item_title><ht:news_item_url>https://news.example/{g}</ht:news_item_url>
<ht:news_item_source>Example</ht:news_item_source></ht:news_item></item>
<item><title>sport match</title><ht:approx_traffic>500+</ht:approx_traffic><pubDate>Mon, 2 Nov 2026 10:00:00 -0800</pubDate>
</item></channel></rss>"""


def test_viral_google_trends_become_posts_the_ai_sees(monkeypatch):
    """Owner 2026-11-02: «находить виральные новости, которые предлагает сам гугл»."""
    import io
    import urllib.request

    from addons.news import viral

    feeds = {"DE": dict(q="Bundeswehr Wehrpflicht", t="200K+", n="Bundestag beschließt neue Wehrpflicht für Junge", g="de"),
             "RU": dict(q="тарифы жкх", t="20000+", n="Минстрой предложил ввести надбавки к платежам за ЖКХ", g="ru")}

    def fake_open(req, timeout=0):
        geo = req.full_url.rsplit("=", 1)[1]
        return io.BytesIO(GOOGLE_RSS.format(**feeds[geo]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    trends = viral.google_trends(["DE", "RU"])
    got = [(t["geo"], t["query"], t["traffic"]) for t in trends]
    assert len(got) == 4 and ("DE", "Bundeswehr Wehrpflicht", 200_000) in got and ("RU", "тарифы жкх", 20_000) in got
    posts = viral.trend_posts(trends)
    de = next(p for p in posts if p["geo"] == "DE" and p["traffic"] == 200_000)
    assert de["src"] == "google" and de["views"] == 0 and "Wehrpflicht für Junge" in de["text"]
    assert de["link"] == "https://news.example/de"
    # a trend with no news has no text to tell — it drops out as «мало текста»
    now = 1_793_640_000.0
    posts = [dict(p, ts=now - 3600) for p in posts]
    filler = [_vpost("feed", i, 1000, t, 5, now) for i, t in enumerate(_filler(110), 1)]
    ranked = viral.rank(filler + posts, now, viral.WEIGHTS, [], [], set(), set(), use_ai=False)
    st = next(s for s in ranked if s["seed"].get("geo") == "DE")
    assert st["region"] == "world" and st["place"] == "Германия" and st["signals"]["google"] == ["DE 200 000+"]
    assert "Google Trends — сейчас ищут: DE 200 000+" in viral.story_block(1, st)
    assert "Google Trends DE 200 000+" in viral.triage_line(1, st)
    assert not any(s["seed"].get("ref", "").endswith("sport match") and not s["skip"] for s in ranked)


def test_viral_tiktok_check_searches_the_best_stories_once_a_day(tmp_path, monkeypatch):
    """Owner 2026-11-02: «находить новость, которая залетела… может apify» — Apify's TikTok search, off by default."""
    import io
    import json as _json
    import types
    import urllib.request

    from addons.news import viral

    monkeypatch.setattr(viral, "DATA", tmp_path)
    now = 1_793_640_000.0
    stories = [{"key": f"k{i}", "place": "Россия", "region": "russia",
                "posts": [{"views": 10, "plain": f"Сюжет {i}: блокировки VPN"}]} for i in range(1, 4)]
    assert viral.tiktok_check(stories) == 0 and "tiktok" not in stories[0]           # off without NEWS_TIKTOK
    monkeypatch.setenv("NEWS_TIKTOK", "1")
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    monkeypatch.setenv("CODEX_GW_SECRET", "x")
    asked = []
    fake_llm = types.SimpleNamespace(chat_json=lambda *a, **k: [{"id": 1, "q": "блокировка VPN"}, {"id": 2, "q": "тарифы ЖКХ"}])
    real = viral._channels_mod
    monkeypatch.setattr(viral, "_channels_mod", lambda name: fake_llm if name == "llm" else real(name))

    def fake_open(req, timeout=0):
        body = _json.loads(req.data)
        asked.append((body["keywords"][0], req.headers.get("Authorization")))
        items = [{"views": "954551", "likes": "1", "comments": "2", "shares": "3", "uploadedAt": str(int(now - 7200)),
                  "title": "Что делать, если глушат интернет", "postPage": "https://www.tiktok.com/@x/video/1"},
                 {"views": "1200", "uploadedAt": str(int(now - 3600)), "title": "другое", "postPage": ""}]
        return io.BytesIO(_json.dumps(items).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    assert viral.tiktok_check(stories, top=2) == 2
    assert sorted(asked) == [("блокировка VPN", "Bearer test-token"), ("тарифы ЖКХ", "Bearer test-token")]
    assert stories[0]["tiktok"]["videos"][0]["views"] == 954_551
    assert "TikTok за неделю по запросу «блокировка VPN»: 954 551 просм., 2 ч назад" in viral.tiktok_lines(stories[0], now)
    assert "tiktok" not in stories[2]                                                 # only the best `top`
    # the next pick within a day reuses the cache: no new query
    again = [dict(st, tiktok=None) for st in stories[:2]]
    for st in again:
        st.pop("tiktok")
    asked.clear()
    assert viral.tiktok_check(again, top=2) == 2 and asked == []
    # the month cap stops new queries
    monkeypatch.setenv("NEWS_TIKTOK_MONTH_QUERIES", "2")
    assert viral.tiktok_check([{"key": "new", "place": "", "region": "russia", "posts": [{"views": 1, "plain": "x"}]}]) == 0
    assert asked == []


def test_tiktok_pieces_are_short_silent_cropped_and_a_film_takes_few(tmp_path, monkeypatch):
    """Owner 2026-11-02: footage from TikTok, «самое критичное 3-е» — TikTok cuts the reach of re-uploads."""
    import json as _json
    import subprocess
    import sys as _sys
    import types

    from addons.news import build, yt_footage

    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=576x1024:rate=25", "-f", "lavfi",
                    "-i", "sine=frequency=440", "-t", "6", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(src)],
                   check=True)
    row = {"url": "https://www.tiktok.com/@hessenschau/video/77", "author": "hessenschau", "name": "hessenschau",
           "title": "Gen-Z-Proteste in Frankfurt", "ts": 1793600000, "views": 248599}
    monkeypatch.setattr(yt_footage, "tt_item", lambda url, fresh=False: row)
    monkeypatch.setattr(yt_footage, "tt_video", lambda url: src)
    monkeypatch.setitem(_sys.modules, "library", types.SimpleNamespace(add=lambda *a, **k: None))
    out = yt_footage.tt_cut(row["url"], 1.0, 4.0, tmp_path / "media" / "frankfurt.mp4")
    assert out == tmp_path / "media" / "tt" / "frankfurt.mp4"                     # always in media/tt/
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "json",
                            str(out)], capture_output=True, text=True).stdout
    streams = _json.loads(probe)["streams"]
    assert [s["codec_type"] for s in streams] == ["video"]                          # no sound
    assert streams[0]["width"] < 576 and streams[0]["height"] < 1024                # the edges cropped
    meta = _json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["credit"] == "" and meta["author"] == "@hessenschau" and meta["tiktok"] is True   # no credit
    with pytest.raises(SystemExit):
        yt_footage.tt_cut(row["url"], 0.0, 5.0, tmp_path / "media" / "long.mp4")      # > 4 s

    def story(*srcs):
        return {"shots": [({"src": s} if not s.startswith("clean:") else
                           {"src": "media/clean/x.clean.mp4", "src_orig": s[6:]}) for s in srcs]}

    assert build.tiktok_problems(story("media/post.mp4", "media/tt/a.mp4", "clean:media/tt/b.mp4")) == []
    assert any("opens on a TikTok" in w for w in build.tiktok_problems(story("media/tt/a.mp4", "media/post.mp4")))
    four = story("media/post.mp4", "media/tt/a.mp4", "media/tt/b.mp4", "clean:media/tt/c.mp4", "media/tt/d.mp4")
    assert any("4 TikTok pieces" in w for w in build.tiktok_problems(four))
    # always cleaned: «clean: none» does not let a TikTok piece skip clean_footage.py
    assert build.unclean_footage({"shots": [{"src": "media/tt/a.mp4", "clean": "none"},
                                            {"src": "media/yt/b.mp4", "clean": "none"}]}) == ["media/tt/a.mp4"]


def test_no_source_metadata_reaches_a_piece_or_the_film(tmp_path, monkeypatch):
    """Owner 2026-11-02: «в метаданные… чтобы для тт не было тригером» — TikTok's files carry comment=vid:…, vid_md5,
    aigc_info; a piece and the final film keep none."""
    import subprocess
    import sys as _sys
    import types

    from addons.news import build, yt_footage

    src = tmp_path / "tiktok.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x568:rate=25", "-t", "5",
                    "-c:v", "libx264", "-metadata", "comment=vid:v26044gc0000test", "-metadata", "vid_md5=2e20c802",
                    "-metadata", 'aigc_info={"aigc_label_type":0}', "-movflags", "use_metadata_tags", str(src)],
                   check=True)
    assert set(build.foreign_tags(src)) >= {"comment", "vid_md5", "aigc_info"}
    row = {"url": "https://www.tiktok.com/@x/video/1", "author": "x", "name": "x", "title": "", "ts": 0, "views": 0}
    monkeypatch.setattr(yt_footage, "tt_item", lambda url, fresh=False: row)
    monkeypatch.setattr(yt_footage, "tt_video", lambda url: src)
    monkeypatch.setitem(_sys.modules, "library", types.SimpleNamespace(add=lambda *a, **k: None))
    piece = yt_footage.tt_cut(row["url"], 0.5, 3.0, tmp_path / "media" / "tt" / "a.mp4")
    assert build.foreign_tags(piece) == []
    film = tmp_path / "final.mp4"
    film.write_bytes(src.read_bytes())
    build.clean_metadata(film)
    assert build.foreign_tags(film) == []


def test_a_telegram_video_that_is_a_tiktok_file_is_flagged(tmp_path):
    import subprocess

    from addons.news import build

    (tmp_path / "media").mkdir()
    for name, tags in (("toporlive_206022.mp4", ["-metadata", "comment=vid:v26044gc0000test"]), ("plain.mp4", [])):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x568:rate=25", "-t", "2",
                        "-c:v", "libx264", *tags, str(tmp_path / "media" / name)], check=True)
    story = {"shots": [{"src": "media/toporlive_206022.mp4"}, {"src": "media/plain.mp4"}]}
    w = build.reposted_tiktok(tmp_path, story)
    assert len(w) == 1 and "toporlive_206022.mp4 is a TikTok file" in w[0] and "not the first shot" in w[0]
