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
