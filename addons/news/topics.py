"""What kind of event a post is: accident / crime / detention / fire / dtp / fatal (shared by harvest.py and pick.py).

Every stem matches from the start of a word: «вследствие» is not an investigation, «посуда» is not a court,
«задержка рейса» is not a detention, «перестрелка» still is a shooting.
"""
import re

WS = r"(?<![а-яё])"  # word start (re.I also covers capitals)

# incidents and disasters
ACCIDENT = re.compile(WS + r"(?:пожар|(?:за|с|вы|по|до)?гор(?:ит|ел|ящ|яч)|взрыв|задым|дым\b|ДТП|авар|столкн|обруш|рухн|"
                      r"провал|эвакуац|МЧС|спас(?:ли|атель)|погиб|пострадал|прорыв|прорвал|потоп|затопи|наезд|сбил|упал|"
                      r"выпал|утечк|газ\b|тонул|утонул|обвал|перевернул)", re.I)
# crimes and detentions — the format's focus since 2026-10-27
CRIME = re.compile(WS + r"(?:задержа(?:н|л|ни)|арест|под стражу|уголовн\w* дел|возбуд\w* дел|следств|следовател|"
                   r"суд(?:а|е|у|ом)?(?![а-яё])|судь(?:я|и|ю|ей)(?![а-яё])|осуд|пригов|разыск|розыск|ориентировк|убий|убил|застрел|зарез|"
                   r"перестрел|стрел(?:ял|ьб)|выстрел|напал|напад|нож|поджог|поджёг|ограб|граб[её]ж|грабител|разбой|краж|"
                   r"украл|вор(?:а|ы|ов)?(?![а-яё])|воровств|похит|похищ|мошенн|афер|избил|избиени|побо(?:и|ев)|наркот|"
                   r"закладк|вымогат|взятк|взяточ|коррупц|хищени|контрабанд|заложник|укол|шприц|хулиган|"
                   r"изнасил|растлен|педофил|развращ)", re.I)
DETENTION = re.compile(WS + r"(?:задержа(?:н|л|ни)|арест|под стражу|розыск|разыск|ориентировк)", re.I)
FIRE = re.compile(WS + r"(?:пожар|(?:за|с|вы|по|до)?гор(?:ит|ел|ящ|яч)|поджог|поджёг|задым)", re.I)
DTP = re.compile(WS + r"(?:ДТП|авар|столкн|наезд|сбил|перевернул)", re.I)
FATAL = re.compile(WS + r"(?:погиб|смерт|насмерть|скончал|труп|тел[оа](?![а-яё]))", re.I)

TAGS = (("crime", CRIME), ("detention", DETENTION), ("accident", ACCIDENT), ("fire", FIRE), ("dtp", DTP),
        ("fatal", FATAL))


def tags(text: str) -> list[str]:
    """Event tags, e.g. ["crime", "detention"]; ["other"] when nothing matches."""
    return [name for name, rx in TAGS if rx.search(text)] or ["other"]


def is_event(text: str) -> bool:
    """An incident or a crime — what the harvester keeps."""
    return bool(ACCIDENT.search(text) or CRIME.search(text))
