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
    args = ap.parse_args(argv)

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
