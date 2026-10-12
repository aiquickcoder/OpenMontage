"""python -m studio serve [--port 4760] | hash-password"""

from __future__ import annotations

import argparse
import getpass
import sys

from dotenv import load_dotenv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="studio")
    sub = ap.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=4760)
    sub.add_parser("hash-password", help="print a STUDIO_ADMIN_PASSWORD_HASH value")
    imp = sub.add_parser("import-worlds", help="import a world-adaptations zip into Studio")
    imp.add_argument("zip")
    args = ap.parse_args(argv)

    if args.cmd == "import-worlds":
        from pathlib import Path

        load_dotenv()
        from studio import config as studio_config
        from studio.worlds import WorldStore, import_archive

        ids = import_archive(WorldStore.of(studio_config.load()), Path(args.zip).read_bytes())
        print("\n".join(ids))
        return 0

    if args.cmd == "hash-password":
        from studio.auth import hash_password

        pw = getpass.getpass("Пароль: ")
        if len(pw) < 10:
            print("Нужно минимум 10 символов", file=sys.stderr)
            return 1
        if pw != getpass.getpass("Ещё раз: "):
            print("Пароли не совпадают", file=sys.stderr)
            return 1
        print(hash_password(pw))
        return 0

    load_dotenv()
    import uvicorn

    from studio.app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
