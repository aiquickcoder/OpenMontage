"""addons/channels: text filters, story clustering, schedule, post format, crop plan, routing, feeds, bot buttons."""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from addons.channels import cluster, config, filters, judge, rewrite, schedule
from addons.channels.db import DB


# -- filters -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Силы ПВО за ночь сбили 235 беспилотников, — Минобороны РФ.",
    "ВСУ атаковали дронами Белгородскую область",
    "Участник СВО вернулся домой и открыл кафе",
    "В Курской области после атаки БПЛА загорелся дом",
    "Военкоматы начали рассылать электронные повестки",
    "Обмен пленными состоялся на границе",
    "Ракетный удар по Киеву",
    "Задержан подозреваемый в госизмене",
])
def test_war_is_out(text):
    assert filters.is_war(text)
    assert "война/СВО" in filters.verdict(text)


@pytest.mark.parametrize("text", [
    "Мужчина нанёс удар ножом соседу в Казани",
    "Повестка дня заседания Мосгордумы",
    "УАЗ Patriot перевернулся на МКАД",
    "Солнечный удар получили трое туристов",
    "Теннисист сломал ракетку о корт",
    "В Москве горит склад на Варшавском шоссе",
])
def test_ordinary_news_is_not_war(text):
    assert not filters.is_war(text)


@pytest.mark.parametrize("text", [
    "Розыгрыш iPhone среди подписчиков! Условия в закрепе",
    "Реклама. ООО «Ромашка» erid: 2Vtzqv",
    "Скидки до 70% только сегодня по промокоду ZASN",
    "Подписывайтесь на наш канал про путешествия",
    "Заработок от 5000 в день — пиши в @cash_bot",
])
def test_ads_are_out(text):
    assert filters.is_ad(text)


def test_propaganda_and_risky():
    assert filters.is_propaganda("Захарова заявила, что Запад снова лжёт")
    assert filters.is_propaganda("Песков: Кремль не видит смысла в переговорах")
    assert not filters.is_propaganda("Иван Захаров задержан за кражу велосипеда в Казани")
    assert "суицид" in filters.verdict("Подросток покончил с собой")
    assert filters.verdict("Путин подписал закон о штрафах для водителей") == []


def test_plain_drops_tails_links_and_markup():
    text = "🔥 **Горит склад** в Москве\n\nПодробности [тут](https://t.me/x)\n\nПодписаться на Mash"
    assert filters.plain(text) == "Горит склад в Москве\nПодробности тут"


# -- clustering ----------------------------------------------------------------------------------------------------------
FEED = [
    "Водитель такси сбил пешехода на Ленинском проспекте",
    "В Москве открыли новую станцию метро",
    "Синоптики обещают снег в выходные",
    "Курьер на самокате врезался в автобус в Химках",
    "Цены на бензин выросли за неделю на 2%",
    "Автомобиль вылетел на тротуар на Тверской улице",
    "Полиция задержала мошенников, обманувших пенсионеров",
    "Водитель автомобиля уснул за рулём и съехал в кювет",
]


def _idx(texts):
    return cluster.Index(cluster.signature(t) for t in texts)


def test_same_fire_from_two_channels_is_one_story():
    a = cluster.signature("В Казани на улице Баумана загорелся двухэтажный дом, пострадавших нет. Огонь потушили за 40 минут")
    b = cluster.signature("Пожар на Баумана: в Казани горел двухэтажный жилой дом, пожарные справились за 40 минут")
    idx = _idx(FEED + ["x"])
    idx.add(a)
    idx.add(b)
    assert cluster.same_event(a, b, idx)


def test_two_different_crashes_stay_apart():
    a = cluster.signature("Водитель автомобиля сбил пешехода на Ленинском проспекте в Москве")
    b = cluster.signature("Водитель автомобиля сбил самокатчика на Профсоюзной улице в Москве")
    idx = _idx(FEED * 3)
    idx.add(a)
    idx.add(b)
    assert not cluster.same_event(a, b, idx)


def test_best_story_picks_closest():
    idx = _idx(FEED * 2)
    fire = cluster.signature("Склад маркетплейса загорелся в Подольске, площадь пожара 20 тысяч квадратных метров")
    other = cluster.signature("В Подольске открыли новый детский сад на 300 мест")
    new = cluster.signature("Пожар на складе маркетплейса в Подольске: огонь охватил 20 тысяч квадратных метров")
    for s in (fire, other, new):
        idx.add(s)
    assert cluster.best_story(new, [(1, other), (2, fire)], idx) == 2


# -- schedule --------------------------------------------------------------------------------------------------------------
CH = {"key": "news", "per_day": 31, "hours": config.HOURS["news"], "quiet_from": 1, "quiet_to": 7, "min_gap_min": 8,
      "paused": 0, "live_since": 0, "ramp": 0.5}


def _msk(h, m=0):
    return datetime(2026, 11, 2, h, m, tzinfo=schedule.MSK)


def test_owed_grows_through_the_day():
    assert schedule.owed(_msk(6, 59), 31, CH["hours"]) <= 1
    noon = schedule.owed(_msk(12), 31, CH["hours"])
    assert 8 <= noon <= 13
    assert schedule.owed(_msk(23, 59), 31, CH["hours"]) == 31


def test_may_post_respects_owed_gap_quiet_and_pause():
    now = _msk(12)
    ok, _ = schedule.may_post(now, CH, 3, now.timestamp() - 3600)
    assert ok
    ok, why = schedule.may_post(now, CH, 3, now.timestamp() - 120)
    assert not ok and "интервал" in why
    ok, why = schedule.may_post(now, CH, 30, now.timestamp() - 3600)
    assert not ok and "графику" in why
    ok, why = schedule.may_post(_msk(3), CH, 0, None)
    assert not ok and "тихие" in why
    ok, why = schedule.may_post(now, {**CH, "paused": 1}, 0, None)
    assert not ok and "пауз" in why


def test_urgent_skips_schedule_and_quiet_hours_but_keeps_short_gap():
    night = _msk(3)
    assert schedule.may_post(night, CH, 99, night.timestamp() - 600, urgent=True)[0]
    assert not schedule.may_post(night, CH, 0, night.timestamp() - 60, urgent=True)[0]


def test_first_live_day_runs_at_half():
    now = _msk(23, 59)
    assert schedule.owed(now, 30, CH["hours"], live_since=now.timestamp() - 3600, ramp=0.5) == 15
    assert schedule.owed(now, 30, CH["hours"], live_since=now.timestamp() - 2 * 86400, ramp=0.5) == 30


def test_profile_from_references():
    prof = schedule.profile_from([[1] * 12 + [3] * 12, [0] * 12 + [2] * 12])
    assert len(prof) == 24 and abs(sum(prof) - 24) < 0.6 and prof[20] > prof[3]


# -- post format ------------------------------------------------------------------------------------------------------------
NEWS = {"key": "news", "username": "zasnyali_news", "title": "ЗАСНЯЛИ Live", "cta": "ЗАСНЯЛИ Live. Подписаться"}
MSKCH = {"key": "moscow", "username": "zasnyali_moscow", "title": "ЗАСНЯЛИ МОСКВА Live", "cta": "ЗАСНЯЛИ МОСКВА. Подписаться"}


def _post(**kw):
    d = {"emoji": "⚡️", "headline": "Склад загорелся в Подольске", "who": "МЧС",
         "paragraphs": ["Площадь пожара — 20 тысяч квадратных метров.", "Пострадавших нет."], "comment": ""}
    d.update(kw)
    return d


def test_render_has_emoji_bold_headline_and_own_channel_link_last():
    h = rewrite.render(_post(), NEWS)
    assert h.startswith("⚡️<b>Склад загорелся в Подольске</b>, — МЧС.")
    assert h.endswith('👉 <a href="https://t.me/zasnyali_news">ЗАСНЯЛИ Live. Подписаться</a>')
    assert h.count("\n\n") == 3
    assert rewrite.problems(h, NEWS) == []


def test_render_escapes_html_and_keeps_city_comment():
    h = rewrite.render(_post(headline="Цена <b>выросла</b> & всё", who="", comment="Тишина в библиотеке!"), MSKCH)
    assert "&lt;b&gt;выросла&lt;/b&gt; &amp;" in h and "<i>Тишина в библиотеке!</i>" in h
    assert rewrite.render(_post(comment="лишнее"), NEWS).count("<i>") == 0


@pytest.mark.parametrize("para,word", [
    ("Об этом сообщил Mash.", "источник"),
    ("Подробности у ТАСС.", "источник"),
    ("Видео — в канале @moscowach.", "ссылки"),
    ("Смотрите t.me/topor.", "ссылки"),
    ("Это настоящий шок для жителей.", "шок"),
    ("Задержанный убийца молчит.", "обвинение"),
    ("Его телефон +7 916 123-45-67.", "личные"),
    ("Машина с номером А123ВС777 скрылась.", "личные"),
    ("После атаки беспилотников начался пожар.", "военная"),
])
def test_problems_catch_source_names_links_shock_guilt_personal_data_war(para, word):
    h = rewrite.render(_post(paragraphs=[para]), NEWS)
    assert any(word in p for p in rewrite.problems(h, NEWS)), rewrite.problems(h, NEWS)


def test_problems_allow_axe_scammers_and_verdict():
    for para in ("Мужчина напал на прохожего с топором.", "Телефонные мошенники обманули пенсионерку на 2 млн.",
                 "Суд вынес приговор: убийца получил 18 лет."):
        h = rewrite.render(_post(paragraphs=[para]), NEWS)
        assert rewrite.problems(h, NEWS) == [], (para, rewrite.problems(h, NEWS))


def test_problems_need_cta_of_this_channel_and_short_text():
    h = rewrite.render(_post(), NEWS)
    assert any("подписка" in p for p in rewrite.problems(h, MSKCH))
    long = rewrite.render(_post(paragraphs=["слово " * 100, "слово " * 100]), NEWS)
    assert any("длинно" in p for p in rewrite.problems(long, NEWS))
    assert any("эмодзи" in p for p in rewrite.problems(h.replace("⚡️", "", 1), NEWS))


def test_sources_text_puts_first_hand_first_and_names_no_channel():
    posts = [{"plain": "Пересказ канала", "kind": "aggregator", "views": 900000, "trust": 0.6, "channel": "mash"},
             {"plain": "Сообщение МЧС", "kind": "primary", "views": 100, "trust": 0.9, "channel": "mchs_official"}]
    s = rewrite.sources_text(posts)
    assert s.index("Сообщение МЧС") < s.index("Пересказ канала") and "mash" not in s


# -- media crop plan -------------------------------------------------------------------------------------------------------
def test_crop_cuts_corner_logos_through_nearest_edge():
    from addons.channels import media

    box = media.crop_plan([[900, 20, 1060, 80], [10, 1700, 200, 1890]], 1080, 1920, 0.18)
    assert box is not None
    x, y, w, h = box
    assert y >= 80 and y + h <= 1700 and w == 1080 and h % 2 == 0


def test_crop_refuses_a_logo_in_the_middle_or_too_much():
    from addons.channels import media

    assert media.crop_plan([[400, 800, 700, 1000]], 1080, 1920, 0.18) is None
    assert media.crop_plan([[0, 0, 1080, 340], [0, 1580, 1080, 1920]], 1080, 1920, 0.18) is None


def test_merge_boxes_joins_overlaps():
    from addons.channels import media

    assert media.merge_boxes([[0, 0, 10, 10], [8, 8, 20, 20], [100, 100, 110, 110]]) == [[0, 0, 20, 20], [100, 100, 110, 110]]


# -- judge score and routing -----------------------------------------------------------------------------------------------
SIG = {"channels": 4, "reach": 900000, "age_h": 0.5, "video": True, "photo": False, "primary": True, "trust": 0.9}


def test_score_zero_for_flags_and_higher_for_spread():
    v = {"relevance": 8, "significance": 7, "virality": 8}
    assert judge.score({**v, "war": True}, SIG) == 0
    assert judge.score(v, SIG) > judge.score(v, {**SIG, "channels": 1, "reach": 5000, "primary": False})


def test_channels_for_routes_by_region():
    chans = [dict(c) for c in config.CHANNELS]
    assert judge.channels_for({"region": "moscow"}, 7.1, chans) == ["news", "moscow"]
    assert judge.channels_for({"region": "moscow"}, 5.5, chans) == ["moscow"]
    assert judge.channels_for({"region": "kazan"}, 5.0, chans) == ["kazan"]
    assert judge.channels_for({"region": "world"}, 7.0, chans) == ["news"]
    assert judge.channels_for({"region": "world"}, 0, chans) == []


# -- feeds and albums ---------------------------------------------------------------------------------------------------------
def test_rss_items_parse_title_text_time_image():
    from addons.channels import collect

    raw = b"""<?xml version="1.0"?><rss><channel><item><title>\xd0\x9f\xd0\xbe\xd0\xb6\xd0\xb0\xd1\x80</title>
    <link>https://x.ru/1</link><description>&lt;p&gt;Text here&lt;/p&gt;</description>
    <pubDate>Sun, 01 Nov 2026 12:00:00 +0300</pubDate><enclosure url="https://x.ru/1.jpg" type="image/jpeg"/></item>
    </channel></rss>"""
    it = collect.rss_items(raw)[0]
    assert it["link"] == "https://x.ru/1" and it["text"] == "Пожар. Text here"
    assert it["media"] == [{"kind": "photo", "url": "https://x.ru/1.jpg"}]
    assert abs(it["ts"] - datetime(2026, 11, 1, 9, 0).timestamp() + time.timezone) < 86400


def test_albums_fold_into_one_post_with_every_item():
    from addons.channels import tgread

    rows = [{"post": 11, "grouped": 5, "raw": "", "media": "photo", "size": 1, "duration": 0, "views": 10, "date": "a"},
            {"post": 10, "grouped": 5, "raw": "Текст", "media": "video", "size": 2, "duration": 9, "views": 30, "date": "a"},
            {"post": 12, "grouped": 0, "raw": "Один", "media": "", "size": 0, "duration": 0, "views": 1, "date": "b"}]
    out = tgread.fold_albums(rows)
    assert len(out) == 2
    alb = next(r for r in out if r["post"] == 10)
    assert [m["post"] for m in alb["media"]] == [10, 11] and alb["views"] == 30
    assert next(r for r in out if r["post"] == 12)["media"] == []


# -- bot buttons through the factory bot --------------------------------------------------------------------------------------
class FakeTg:
    chat = "42"
    enabled = True

    def __init__(self):
        self.sent, self.calls = [], []

    def send(self, text, buttons=None):
        self.sent.append(text)

    def call(self, method, **params):
        self.calls.append((method, params))
        return {}


def test_pause_button_pauses_channel_and_updates_keyboard(tmp_path, monkeypatch):
    from addons.channels import seed
    from factory import service

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "c.db")
    d = DB(config.DB_PATH)
    seed.seed(d)
    pid = d.insert("posts", {"story_id": 1, "channel": "news", "status": "published", "created": time.time()})
    tg = FakeTg()
    cq = {"id": "q", "data": "chpause:news", "message": {"chat": {"id": 42}, "message_id": 7, "reply_markup": {
        "inline_keyboard": [[{"text": "🗑 Удалить", "callback_data": f"chdel:{pid}"},
                             {"text": "⏸ Пауза канала", "callback_data": "chpause:news"}]]}}}
    service.handle_update(None, None, tg, {"callback_query": cq})
    assert DB(config.DB_PATH).channel("news")["paused"] == 1
    edit = next(p for m, p in tg.calls if m == "editMessageReplyMarkup")
    assert edit["reply_markup"]["inline_keyboard"][0][1]["callback_data"] == "chresume:news"
    assert any(m == "answerCallbackQuery" for m, _ in tg.calls)
    cq["data"] = "chresume:news"
    service.handle_update(None, None, tg, {"callback_query": cq})
    assert DB(config.DB_PATH).channel("news")["paused"] == 0


def test_delete_button_removes_a_preview_from_the_queue(tmp_path, monkeypatch):
    from addons.channels import bot_actions, seed

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "c.db")
    d = DB(config.DB_PATH)
    seed.seed(d)
    pid = d.insert("posts", {"story_id": 1, "channel": "kazan", "status": "previewed", "created": time.time()})
    note, _ = bot_actions.handle("chdel", str(pid))
    assert "Снято" in note and DB(config.DB_PATH).one("SELECT status FROM posts WHERE id=?", (pid,))["status"] == "rejected"


def test_buttons_from_a_stranger_chat_are_ignored(tmp_path, monkeypatch):
    from factory import service

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "c.db")
    tg = FakeTg()
    service.handle_update(None, None, tg, {"callback_query": {"id": "q", "data": "chpause:news",
                                                             "message": {"chat": {"id": 13}}}})
    assert tg.calls == [] and not (tmp_path / "c.db").exists()


def test_digest_posts_are_recognised():
    assert cluster.is_digest("Главное за день:\n▪ Иран признал ВМФ террористами\n▪ Умер бэк-вокалист «Любэ»\n"
                             "▪ Шнайдер выиграла турнир\n▪ Снег в среду")
    assert not cluster.is_digest("Пожар на складе.\nПлощадь 20 тысяч метров.\nПострадавших нет.")


def test_a_chain_does_not_glue_another_event_to_a_story():
    idx = _idx(FEED * 2)
    seed = cluster.signature("Путин ответит на звонок Зеленского, заявил помощник президента Ушаков")
    bridge = cluster.signature("Ушаков рассказал о звонке Зеленского и о том, что российский дизель попадает в Европу "
                               "под другими марками, как сказал Новак")
    new = cluster.signature("Новак: российский дизель попадает на рынки Европы под другими марками")
    for s in (seed, bridge, new):
        idx.add(s)
    assert cluster.best_story(new, [(1, seed), (1, bridge)], idx, seeds={1: seed}) is None


def test_fresh_strong_story_may_run_ahead_of_schedule_but_not_inside_the_gap():
    now = _msk(12)
    need = schedule.owed(now, 31, CH["hours"])
    assert not schedule.may_post(now, CH, need, now.timestamp() - 3600)[0]
    ok, why = schedule.may_post(now, CH, need, now.timestamp() - 3600, burst=2)
    assert ok and "раньше графика" in why
    assert not schedule.may_post(now, CH, need + 2, now.timestamp() - 3600, burst=2)[0]
    assert not schedule.may_post(now, CH, need, now.timestamp() - 60, burst=2)[0]


def test_primary_source_bonus_is_soft_and_configurable():
    v = {"relevance": 3, "significance": 2, "virality": 1}  # a dull agency report
    with_primary = judge.score(v, {**SIG, "channels": 1, "reach": 3000, "video": False})
    assert with_primary < 6.0  # still under the main channel's bar
    assert judge.score(v, {**SIG, "channels": 1}, primary_bonus=2.0) > judge.score(v, {**SIG, "channels": 1}, primary_bonus=0)


def test_json_of_takes_the_whole_array_and_salvages_a_cut_one():
    from addons.channels import llm

    assert llm.json_of('```json\n[{"id": 1}, {"id": 2, "t": "a]b{"}]\n```') == [{"id": 1}, {"id": 2, "t": "a]b{"}]
    assert llm.json_of('Вот ответ: {"ok": true, "list": [1, 2]} конец') == {"ok": True, "list": [1, 2]}
    assert llm.json_of('[{"id": 1}, {"id": 2}, {"id": 3, "tit') == [{"id": 1}, {"id": 2}]
    with pytest.raises(llm.LLMError):
        llm.json_of("нет json")


def test_problems_catch_talk_about_sources():
    h = rewrite.render(_post(paragraphs=["Точного прогноза в источнике нет."]), NEWS)
    assert any("источник" in p for p in rewrite.problems(h, NEWS))
