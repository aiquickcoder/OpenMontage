"""What the ЗАСНЯЛИ channels never carry, decided on the text alone (pure functions, tested): the war and the «СВО»,
ads and giveaways, state propaganda, the topics the law makes risky. The AI judge (judge.py) checks the same things
by meaning; a post must pass both.

Patterns are word-start anchored where a short stem would catch innocent words: «удар ножом» is a crime, not a
strike; «ракетка» is tennis; «дрон-доставка» is rare enough to lose. Built on addons/news/world.py (WAR, RU_STATE,
ADS) and harvest_top.py (WARPOL, NOISE), widened for a feed that takes the whole country.
"""
from __future__ import annotations

import re

WS = r"(?<![а-яёa-z])"  # word start

# the war, the front, its weapons and its people (owner 2026-11-01: «Без войны и СВО … законы о «фейках» об армии»)
WAR = re.compile(
    WS + r"(?:СВО(?![а-яё])|спецоперац|специальн\w* военн\w* операц|ВСУ(?![а-яё])|всушник|укровоен|украин|Украин|"
    r"Зеленск|Киев|киевск|Харьков|Одесс|Днепропетр|Запорож|Херсон|Донбас|Донецк|Луганск|ДНР(?![а-яё])|ЛНР(?![а-яё])|"
    r"Мариупол|Бахмут|Артёмовск|Артемовск|Авдеевк|Купянск|Покровск\w* направлен|Красноармейск|Алешк|Энергодар|"
    r"фронт(?!ал|ир|енд|мен)|передов\w* (?:позиц|лини)|линии? (?:боевого )?соприкоснов|наступлени|контрнаступ|"
    r"обстрел|артобстрел|ракетн\w* (?:удар|атак|опасн)|(?:нанес|наносит|нанесли|нанёс)\w* удар|удар\w* по (?:территор|объект|город|позиц|инфраструкт)|"
    r"БПЛА|беспилот|дрон(?!-доставк)|коптер|ПВО(?![а-яё])|сбит\w* (?:над|в небе)|воздушн\w* тревог|ракетн\w* опасн|"
    r"ракет(?!к)|кассетн|баллистич|Искандер|Калибр\w* (?:удар|ракет)|ХАРМ|HIMARS|ATACMS|Storm Shadow|ЗРК|Leopard|Abrams|"
    r"мобилизац|мобилизов|военкомат|повестк\w* (?:в военкомат|о призыве|из военкомат)|электронн\w* повестк|контрактник|контракт\w* (?:с|на службу в) (?:Минобороны|армией)|"
    r"военнослужащ|участник\w* СВО|ветеран\w* СВО|боец|бойцы|бойцов|штурмовик|штурмов\w* отряд|военкор|"
    r"Минобороны|министерств\w* обороны|Генштаб|Белоусов|Герасимов|Сырский|ВС РФ|Вооружённ\w* Сил|Вооруженн\w* Сил|"
    r"зон\w* (?:СВО|боевых действий|спецоперации)|приграничь|Белгородск\w* област\w*.{0,40}(?:атак|удар|обстрел)|"
    r"Курск\w* област\w*.{0,40}(?:атак|удар|обстрел|ВСУ)|диверсант|диверси|госизмен|измен\w* Родин|"
    r"НАТО(?![а-яё])|ядерн\w* (?:оруж|удар|угроз)|перемири|мирн\w* (?:переговор|план|соглашен)|"
    r"фейк\w* об армии|дискредитац|военн\w* (?:помощ|сводк|эксперт|корреспонд|техник|преступлен)|"
    r"вооружени|истребител|бомбардир|танк(?![а-яё]|ер)|танков|снаряд|боеприпас|минометн|миномёт|гаубиц|"
    r"военнопленн|плен(?![а-яё])|пленн|обмен\w* пленн|Wagner|ЧВК|Вагнер|Пригожин|Ахмат\w* (?:спецназ|батальон|подразделен)|Кадыров\w* (?:боец|спецназ))",
    re.I)

# a blunt «удар» that is a crime or an accident, kept apart from the strikes above
NOT_WAR = re.compile(r"удар\w* (?:нож|током|молни|кулак|по голове|битой|в лицо)|солнечн\w* удар|тепловой удар", re.I)

# ads, giveaways, native integrations, «subscribe to my other channel»
AD = re.compile(
    r"(?:^|\W)(?:erid|ERID)\b|#реклам|(?<![а-яё])реклама(?![а-яё])|на правах рекламы|партн[её]рск\w* (?:материал|пост)|"
    r"промокод|розыгрыш|разыгрыва|конкурс\w* (?:для подписчик|среди подписчик)|giveaway|"
    r"подпис\w+ на (?:наш|канал|мой)|переходи\w* по ссылке|ссылк\w* в (?:профил|описани|шапк)|"
    r"скидк\w* (?:до|\d)|(?<![а-яё])кешб[эе]к|казино|букмекер|ставк\w* на спорт|фрибет|"
    r"(?<![а-яё])крипт(?:о|а|у)(?![а-яё])|airdrop|заработ\w* (?:от|до) \d|пассивн\w* доход|"
    r"курс\w* (?:по|обучени)|вебинар|марафон\w* (?:похуден|желан)|"
    r"@\w*bot\b|t\.me/\w*bot\b|Реклама\. ООО|ИНН \d{10}", re.I)

# the state talking, not an event: officials' statements and «geopolitics of leaders»
PROPAGANDA = re.compile(
    WS + r"(?:Мари\w* Захаров|Захаров\w* (?:заявил|назвал|ответил|высмеял|прокомментир)|Лавров|Песков|Медведев\w* (?:заявил|написал|пригрозил|ответил)|Небензя|Ушаков|"
    r"Соловь[её]в\w* (?:заявил|сказал|назвал)|Скабеев|Симоньян|Кремл\w* (?:заявил|ответил|прокомментир|назвал)|"
    r"в Кремле (?:заявил|ответил|прокомментир|назвал)|МИД (?:РФ|России)\w* (?:заявил|ответил|назвал)|"
    r"Путин\w* (?:заявил|назвал|ответил|рассказал|пообещал|поручил|провёл|провел|встретил|обсудил)|"
    r"русофоб|антироссийск|западн\w* (?:элит|пропаганд)|коллективн\w* Запад)",
    re.I)

# legally risky or never taken (pick.py SKIP for the news films, narrowed to what the law forbids to detail)
RISKY = {
    "суицид": re.compile(r"суицид|самоубий|покончил\w* с собой|свёл счёты|свел счеты|выпрыгнул\w* из окна", re.I),
    "сексуальное насилие над детьми": re.compile(
        r"(?:изнасил|растлен|развращ|педофил|домога)\w*.{0,80}(?:ребён|ребен|малолетн|несовершеннолет|школьниц|"
        r"школьник|девочк|мальчик|\d{1,2}-летн)|(?:ребён|ребен|малолетн|несовершеннолет|школьниц|девочк|мальчик|"
        r"\d{1,2}-летн)\w*.{0,80}(?:изнасил|растлен|развращ|педофил|домога)", re.I),
    "экстремизм/иноагенты": re.compile(r"иноагент|иностранн\w* агент|экстремистск\w* (?:организац|сообществ)|"
                                       r"нежелательн\w* организац|Навальн|ФБК(?![а-яё])|террорист\w* организац", re.I),
    "выборы/партии": re.compile(r"(?<![а-яё])выбор(?:ы|ов|ах|ам)(?![а-яё]) (?:в Госдуму|губернатор|мэр|президент)|"
                                r"предвыборн|избирательн\w* (?:кампан|участ|комисс)", re.I),
}

# a channel's own tail: subscribe lines, «прислать новость», its other platforms
TAIL = re.compile(
    r"(?im)^\s*(?:[^\w\n]{0,4})(?:подпис(?:аться|ывайтесь|ывайся|ка)\b.*|прислать новость.*|предложить новость.*|"
    r"(?:наш|мы в) (?:max|макс|вк|vk|дзен|telegram)\b.*|\w+ в (?:max|макс|telegram)\s*$|"
    r"(?:топор|mash|baza|shot|readovka|осторожно,? новости)\b[^\n]{0,30}$)")
LINKS = re.compile(r"\[([^\]]*)\]\((?:https?://|tg://)[^)]+\)")
URL = re.compile(r"https?://\S+|t\.me/\S+|@\w{4,}")
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


def is_war(text: str) -> bool:
    t = NOT_WAR.sub(" ", text or "")
    return bool(WAR.search(t))


def is_ad(text: str) -> bool:
    return bool(AD.search(text or ""))


def is_propaganda(text: str) -> bool:
    return bool(PROPAGANDA.search((text or "")[:400]))  # the lead: a post about an official's words


def risky(text: str) -> list[str]:
    return [name for name, rx in RISKY.items() if rx.search(text or "")]


def verdict(text: str) -> list[str]:
    """Why a post is out ([] = it may go on to the AI judge)."""
    why = []
    if is_war(text):
        why.append("война/СВО")
    if is_ad(text):
        why.append("реклама")
    if is_propaganda(text):
        why.append("госпропаганда")
    why += risky(text)
    return why


def plain(text: str) -> str:
    """A post's words without markdown links, tails, URLs and emoji — what clustering and the judge read."""
    t = LINKS.sub(r"\1", text or "")
    t = TAIL.sub(" ", t)
    t = URL.sub(" ", t)
    t = t.replace("**", "").replace("__", "")
    t = EMOJI.sub(" ", t)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{2,}", "\n", t)).strip()
