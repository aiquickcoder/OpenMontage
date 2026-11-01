"""ЗАСНЯЛИ: automatic news channels (@zasnyali_news, @zasnyali_moscow, @zasnyali_kazan).

    collect.py       zasnyali-collector  — Telegram (updates + catch-up, joining), RSS/sites, media downloads
    engine.py        zasnyali-engine     — clustering (cluster.py), the AI judge (judge.py), rewriting (rewrite.py)
    media_worker.py  zasnyali-media      — clean pictures for posts (media.py)
    publisher.py     zasnyali-publisher  — schedule (schedule.py), trial previews / live posts, owner reports
    bot_actions.py   «Удалить» / «Пауза канала» buttons, handled inside factory-bot.service
    studio/channels.py + studio/web/src/pages/Channels.tsx — the «Каналы» page on studio.luvael.ru
    cli.py           status, check-bots, mode, pause/resume, posts
"""
