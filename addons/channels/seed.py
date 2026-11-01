"""The source base the engine starts with (written into the DB once; the owner edits it on the web page afterwards).

Each row: (platform, ref, title, region, kind, trust, every_min). Checked 2026-11-01 from the server: every Telegram
username resolved through Telethon (research_resolve / research search — subscribers then), every feed answered 200 with
items. kind: aggregator (federal news channels), city (a city's news channels), primary (the authority itself:
police, investigators, prosecutors, courts, emergencies, mayors, transport), media (news agencies and papers).
trust 0..1: how far the engine takes a source's facts as given — a primary source's word beats a retelling.

Left out on purpose: media marked foreign agents or undesirable, the war-correspondent channels, anonymous «ЧП»
copies with a few hundred readers, RT (opinion more than events). MAX channels are not read (see README «MAX»).
"""
from __future__ import annotations

import time

# (platform, ref, title, region, kind, trust, every_min)
SOURCES: list[tuple] = [
    # --- federal aggregators (the main channel's competitors and its feed) ---
    ("telegram", "toporlive", "Топор Live", "russia", "aggregator", 0.6, 3),
    ("telegram", "topor", "ТОПОР — Горячие новости", "russia", "aggregator", 0.55, 5),
    ("telegram", "mash", "Mash", "russia", "aggregator", 0.65, 3),
    ("telegram", "bazabazon", "Baza", "russia", "aggregator", 0.65, 3),
    ("telegram", "shot_shot", "SHOT", "russia", "aggregator", 0.6, 3),
    ("telegram", "readovkanews", "Readovka", "russia", "aggregator", 0.55, 5),
    ("telegram", "ostorozhno_novosti", "Осторожно, новости", "russia", "aggregator", 0.65, 3),
    ("telegram", "bbbreaking", "Раньше всех. Ну почти", "russia", "aggregator", 0.6, 3),
    ("telegram", "ru2ch", "Ньюсач/Двач", "russia", "aggregator", 0.45, 10),
    ("telegram", "varlamov_news", "Varlamov News", "russia", "aggregator", 0.6, 10),
    ("telegram", "signal112_news", "СИГНАЛ 112", "russia", "aggregator", 0.5, 10),
    ("telegram", "novostifacti", "Лента новостей", "russia", "aggregator", 0.45, 15),
    # --- agencies and papers (Telegram) ---
    ("telegram", "rian_ru", "РИА Новости", "russia", "media", 0.8, 5),
    ("telegram", "tass_agency", "ТАСС", "russia", "media", 0.85, 5),
    ("telegram", "interfaxonline", "Интерфакс", "russia", "media", 0.85, 10),
    ("telegram", "rbc_news", "РБК", "russia", "media", 0.8, 5),
    ("telegram", "kommersant", "Коммерсантъ", "russia", "media", 0.8, 10),
    ("telegram", "izvestia", "Известия", "russia", "media", 0.75, 10),
    ("telegram", "truekpru", "Комсомольская правда", "russia", "media", 0.6, 15),
    ("telegram", "ntvnews", "НТВ", "russia", "media", 0.65, 15),
    ("telegram", "gazetaru", "Газета.Ru", "russia", "media", 0.65, 15),
    ("telegram", "aifonline", "АиФ", "russia", "media", 0.6, 20),
    ("telegram", "mk_ru", "Московский комсомолец", "russia", "media", 0.6, 20),
    # --- federal authorities: first-hand ---
    ("telegram", "mediamvd", "МВД Медиа", "russia", "primary", 0.95, 5),
    ("telegram", "IrinaVolk_MVD", "Ирина Волк (МВД)", "russia", "primary", 0.95, 5),
    ("telegram", "sledcom_press", "Следком (СК России)", "russia", "primary", 0.95, 5),
    ("telegram", "mchs_official", "МЧС России", "russia", "primary", 0.9, 5),
    ("telegram", "genprocrf", "Генпрокуратура России", "russia", "primary", 0.95, 10),
    ("telegram", "fssp_gov", "ФССП России", "russia", "primary", 0.9, 20),
    ("telegram", "Mintrans_Russia", "Минтранс России", "russia", "primary", 0.9, 20),
    ("telegram", "favt_ru", "Росавиация", "russia", "primary", 0.9, 10),
    ("telegram", "guobddrus", "Госавтоинспекция России", "russia", "primary", 0.9, 20),
    ("telegram", "rospotrebnadzor_ru", "Роспотребнадзор", "russia", "primary", 0.9, 20),
    ("telegram", "minzdrav_ru", "Минздрав России", "russia", "primary", 0.9, 30),
    ("telegram", "centralbank_russia", "Банк России", "russia", "primary", 0.95, 20),
    ("telegram", "RosgvardOfficial", "Росгвардия", "russia", "primary", 0.85, 30),
    # --- Moscow ---
    ("telegram", "moscowach", "Москвач", "moscow", "city", 0.65, 3),
    ("telegram", "moscowmap", "Новости Москвы (moscowmap)", "moscow", "city", 0.6, 3),
    ("telegram", "mosnow", "Москва сейчас", "moscow", "city", 0.55, 5),
    ("telegram", "ostorozhno_moskva", "Осторожно, Москва", "moscow", "city", 0.6, 3),
    ("telegram", "infomoscow24", "Москва 24", "moscow", "city", 0.75, 5),
    ("telegram", "moslenta", "Мосфид", "moscow", "city", 0.6, 10),
    ("telegram", "novostyy_moskva", "МСК | Новости Москвы", "moscow", "city", 0.5, 15),
    ("telegram", "chpmoscoww", "ЧП / Москва", "moscow", "city", 0.5, 5),
    ("telegram", "dtp777", "ДТП и ЧП Москва и МО", "moscow", "city", 0.5, 10),
    ("telegram", "riamo_ru", "РИАМО (Подмосковье и Москва)", "moscow", "city", 0.65, 15),
    ("telegram", "mos_sobyanin", "Мэр Москвы Сергей Собянин", "moscow", "primary", 0.9, 15),
    ("telegram", "DtRoad", "Дептранс Москвы", "moscow", "primary", 0.9, 10),
    ("telegram", "moscowproc", "Прокуратура Москвы", "moscow", "primary", 0.95, 10),
    ("telegram", "moscowcourts", "Суды общей юрисдикции Москвы", "moscow", "primary", 0.95, 10),
    ("telegram", "skmoscowgsu", "Столичный СК", "moscow", "primary", 0.95, 10),
    ("telegram", "mchsmsk", "МЧС Москвы", "moscow", "primary", 0.9, 10),
    ("telegram", "rospotrebnadzormoscow", "Роспотребнадзор Москва", "moscow", "primary", 0.85, 30),
    # --- Kazan / Tatarstan ---
    ("telegram", "kazancity", "Казань на максималках", "kazan", "city", 0.6, 3),
    ("telegram", "kazan", "КазаньДА", "kazan", "city", 0.6, 3),
    ("telegram", "gokzn", "Go Kazan", "kazan", "city", 0.55, 5),
    ("telegram", "kazanckiy", "Первый Казанский", "kazan", "city", 0.55, 5),
    ("telegram", "tatmediaofficial", "ТАТМЕДИА", "kazan", "media", 0.75, 5),
    ("telegram", "iatatarinform", "Татар-информ", "kazan", "media", 0.8, 10),
    ("telegram", "businessgazeta", "БИЗНЕС Online", "kazan", "media", 0.75, 10),
    ("telegram", "realnoevremya", "Реальное время", "kazan", "media", 0.75, 15),
    ("telegram", "eveningkzn", "Вечерняя Казань", "kazan", "city", 0.6, 10),
    ("telegram", "tnvtv", "ТНВ (Новости Татарстана)", "kazan", "media", 0.8, 10),
    ("telegram", "zhest_dtp16", "ЧП Казань Татарстан (анонимный)", "kazan", "city", 0.45, 5),
    ("telegram", "tatarstansos", "Происшествия Татарстан", "kazan", "city", 0.55, 5),
    ("telegram", "mash_iptash", "Mash Iptash", "kazan", "city", 0.6, 5),
    ("telegram", "kazan_seichas", "Казань Сейчас", "kazan", "city", 0.5, 10),
    ("telegram", "chp16_news", "ЧП Казань новости", "kazan", "city", 0.45, 10),
    ("telegram", "kazan_chp116", "Казань ЧП", "kazan", "city", 0.45, 15),
    ("telegram", "kzn_official", "Мэрия Казани", "kazan", "primary", 0.9, 15),
    ("telegram", "rustamminnikhanov", "Рустам Минниханов", "kazan", "primary", 0.85, 30),
    ("telegram", "su_skr16", "СК по Татарстану", "kazan", "primary", 0.95, 10),
    ("telegram", "prokrt", "Прокуратура Татарстана", "kazan", "primary", 0.95, 10),
    ("telegram", "pristav_tatarstan", "ФССП по Татарстану", "kazan", "primary", 0.9, 30),
    ("telegram", "gibddtatarstan", "Госавтоинспекция Татарстана", "kazan", "primary", 0.9, 10),
    # --- feeds (RSS), every few minutes ---
    ("rss", "https://tass.ru/rss/v2.xml", "ТАСС (RSS)", "russia", "media", 0.85, 4),
    ("rss", "https://ria.ru/export/rss2/archive/index.xml", "РИА Новости (RSS)", "russia", "media", 0.8, 4),
    ("rss", "https://www.interfax.ru/rss", "Интерфакс (RSS)", "russia", "media", 0.85, 4),
    ("rss", "https://rssexport.rbc.ru/rbcnews/news/30/full.rss", "РБК (RSS)", "russia", "media", 0.8, 5),
    ("rss", "https://www.kommersant.ru/rss/news.xml", "Коммерсантъ (RSS)", "russia", "media", 0.8, 5),
    ("rss", "https://lenta.ru/rss/news", "Лента.ру (RSS)", "russia", "media", 0.7, 5),
    ("rss", "https://www.vedomosti.ru/rss/news", "Ведомости (RSS)", "russia", "media", 0.8, 10),
    ("rss", "https://www.kp.ru/rss/allsections.xml", "КП (RSS)", "russia", "media", 0.55, 10),
    ("rss", "https://xn--b1aew.xn--p1ai/news/rss", "МВД России (мвд.рф)", "russia", "primary", 0.95, 4),
    ("rss", "https://mchs.gov.ru/deyatelnost/press-centr/novosti/rss", "МЧС России (сайт)", "russia", "primary", 0.9, 5),
    ("rss", "https://www.m24.ru/rss.xml", "Москва 24 (RSS)", "moscow", "city", 0.75, 4),
    ("rss", "https://www.mos.ru/rss", "mos.ru", "moscow", "primary", 0.9, 10),
    ("rss", "https://www.msk.kp.ru/rss/allsections.xml", "КП Москва (RSS)", "moscow", "city", 0.55, 10),
    ("rss", "https://www.tatar-inform.ru/rss", "Татар-информ (RSS)", "kazan", "media", 0.8, 4),
    ("rss", "https://116.ru/rss-feeds/rss.xml", "116.ru (RSS)", "kazan", "city", 0.65, 5),
    ("rss", "https://kazanfirst.ru/feed", "Kazan First (RSS)", "kazan", "city", 0.6, 10),
    # --- official video and sites (addons/news/sources.py) ---
    ("rutube", "23350159", "МВД России (Rutube)", "russia", "primary", 0.95, 5),
    ("site", "https://sledcom.ru/news", "СК России (sledcom.ru)", "russia", "primary", 0.95, 5),
    ("site", "https://tnv.ru/news/the-incident/", "ТНВ: происшествия (tnv.ru)", "kazan", "media", 0.8, 5),
]


def seed(db) -> int:
    """Insert the base sources and the three channels where missing; returns how many sources were added."""
    from addons.channels import config, schedule

    added = 0
    for platform, ref, title, region, kind, trust, every in SOURCES:
        added += 1 if db.insert("sources", {"platform": platform, "ref": ref, "title": title, "region": region,
                                            "kind": kind, "trust": trust, "every_min": every, "added": time.time()},
                                ignore=True) else 0
    for ch in config.CHANNELS:
        row = dict(ch)
        row.setdefault("hours", schedule.flat_profile(row.get("quiet_from", 1), row.get("quiet_to", 7)))
        db.insert("channels", row, ignore=True)
    return added
