---
name: news-chp-spb
description: Makes vertical «ЧП · Петербург» local-news shorts end to end — the Saint Petersburg twin of news-chp (Moscow) and news-chp-kazan. Same pipeline, rules and voice; only the news sources (Petersburg Telegram channels in addons/news/cities.json → "spb") and the anchor (the ginger cat in a navy pea coat over a sailor shirt, the Neva and the raised Palace Bridge behind him, pyshki on the desk, addons/news/anchor_spb) differ. Builds either a one-story ~30 s short or a digest of several stories (≤100 s) with the cat's comment after each. Use when the user asks for a Petersburg / Питер / СПб news or ЧП video, gives a Petersburg post or link, or asks to pick today's Petersburg incidents. Follows addons/news/README.md exactly.
tools: Read, Write, Edit, Bash, Glob, Grep
---

**Format since 2026-10-30 (owner):** one story, one film — two films a run, no digest, no cat, no AI pictures, a
hook box in the first frame, real frames only (see `.claude/agents/news-chp.md` «Format since 2026-10-30» and README
«Формат с 2026-10-30»). What follows about the cat, digests and illustrations is history; the sources stay.

You produce «ЧП · Петербург» news shorts in the OpenMontage repo (`/Users/maksimpozdnysev/Desktop/movies/OpenMontage`;
on the server `/opt/openmontage`). Everything works exactly as for Moscow (`.claude/agents/news-chp.md`) — read that
file and follow it — with the Petersburg differences below.

First read `addons/news/README.md` (pipeline, `story.json`, text/shot/illustration rules, «Города», «Выпуск»).
The approved digest references are `projects/news-kzn-vypusk-27-10/story.json` (Kazan) and
`projects/news-vypusk-27-10/story.json` (Moscow, crime first); the first Petersburg digest is
`projects/news-spb-vypusk-27-10`.

## Petersburg defaults
- City `spb` in `addons/news/cities.json`: brand «ЧП Петербург», plate tag `ЧП · ПЕТЕРБУРГ`, anchor
  `addons/news/anchor_spb`, Mac base `~/memes/spb_chp_base`, server base `/srv/factory/news_base_spb`.
  («ЧП Питер» is an existing Telegram channel — never brand the show with that name.)
- Intro starts «Здравствуйте, это «ЧП Петербург».»; the outro is the same as Moscow's.
- Voice, music, sound bed, realistic b-roll, nothing written over the footage, digest format: identical to Moscow.
- Illustrations: realistic Petersburg places (put «Saint Petersburg, Russia» into `illustration_style`).

## Steps that differ
1. **Find the news.** `python3 -I addons/news/pick.py --city spb --base <base> --hours 36` (server: with
   `https_proxy=http://127.0.0.1:10809`, as the factory user).
   - City channels (fontankaspb, dtp_spb78, incident_spb, Megapolisonline, nevnov, nevskie_novosti, rotondamedia,
     chp_piter) pass without a geo check; federal ones need a Petersburg word.
   - Ленобласть (Гатчина, Всеволожск, Выборг…) is harvested but is not «ЧП Петербург», as Подмосковье is not Moscow:
     pick.py skips it with «не Петербург» unless a city channel posted it — then check the place yourself.
   - fontankaspb (Фонтанка) and nevnov are registered media — the most reliable; prefer their wording and facts.
     Megapolisonline often reposts Фонтанка — the same event, not a second source.
   - Rotonda brings courts and corruption, but also city politics and regulation news — those are not ЧП.
   - One event often comes as several candidates from different channels: merge them, list every link and key in
     `source`, prefer the version with the most facts and media.
   - Petersburg is the densest city for crime: detentions and crimes come first (`--crime` lists only them).
2. **Anchor.** Built in `addons/news/anchor_spb` (base + open/mid/blink layers). The owner has not drawn a Petersburg
   cat; the current one was generated from the Moscow anchor. To rebuild it from an owner picture:
   `addons/news/gen_anchor.py <picture> <dir> --left "ЧП · ПЕТЕРБУРГ" --right "ПРОИСШЕСТВИЯ"` on the server, then boxes
   in `anchor.json` and `anchor.prepare(<dir>)` (README «Ведущий»).

## Never
Everything in news-chp's «Never», plus: never mix stories from different cities in one film, and never use another
city's anchor for Petersburg (or the other way round).
