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
    events = [
        {"key": "fresh1", "dt": at(3), "channels": ["tatarstansos"], "links": [], "text": "Пожар на складе в Казани"},
        {"key": "old_det", "dt": at(80), "channels": ["tatarstansos"], "links": [],
         "text": "В Казани задержали мужчину, угрожавшего ножом продавцу"},
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
    s = {"anchor": {"intro": "Привет.", "outro": "Пока."}, "narration": {"engine": "fishaudio"},
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
