"""python -m factory worker|bot|add "brief"|status|approve <id>"""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from factory import config, release
from factory.db import BriefStore


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="factory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("worker")
    sub.add_parser("bot")
    add = sub.add_parser("add")
    add.add_argument("text")
    sub.add_parser("status")
    appr = sub.add_parser("approve")
    appr.add_argument("brief_id")
    args = ap.parse_args(argv)

    settings = config.load()
    if args.cmd == "worker":
        from factory.service import worker_loop

        worker_loop(settings)
    elif args.cmd == "bot":
        from factory.service import bot_loop

        bot_loop(settings)
    elif args.cmd == "add":
        from factory.service import parse_brief

        text, options = parse_brief(args.text)
        print(BriefStore(settings.db_path).add(text, options))
    elif args.cmd == "status":
        for row in BriefStore(settings.db_path).list(limit=50):
            print(f"{row['state']:<18} {row['id']}  {row.get('detail') or ''}"[:200])
    elif args.cmd == "approve":
        store = BriefStore(settings.db_path)
        print(release.approve(settings.releases_dir, args.brief_id))
        store.move(args.brief_id, "approved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
