"""Single-owner login: scrypt password hash in .env, HMAC-signed session cookie.

No third-party crypto: hashlib.scrypt + hmac. Unsafe methods also need the
`X-Studio: 1` header, which a cross-site form cannot send (CSRF guard on top of
SameSite=Strict).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Depends, HTTPException, Request

from studio.config import StudioSettings

COOKIE = "studio_session"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
LOGIN_WINDOW_S, LOGIN_MAX_FAILS = 15 * 60, 5
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    # ':' separators: the value lives in .env, which bash sources ('$' would expand)
    return f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}:{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.strip().strip("'\"").split(":")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(bytes.fromhex(digest)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_session(secret: str, user: str, ttl_s: int, now: Optional[float] = None) -> str:
    payload = _b64(json.dumps({"u": user, "exp": int((now or time.time()) + ttl_s),
                               "n": secrets.token_hex(8)}).encode())
    sig = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_session(secret: str, token: str, now: Optional[float] = None) -> Optional[str]:
    try:
        payload, sig = token.split(".")
    except (ValueError, AttributeError):
        return None
    want = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, want):
        return None
    try:
        data = json.loads(_unb64(payload))
    except ValueError:
        return None
    if data.get("exp", 0) < (now or time.time()):
        return None
    return data.get("u")


class LoginLimiter:
    """At most LOGIN_MAX_FAILS failed logins per client IP per window."""

    def __init__(self) -> None:
        self._fails: dict[str, deque] = defaultdict(deque)

    def blocked(self, ip: str, now: Optional[float] = None) -> bool:
        now = now or time.time()
        q = self._fails[ip]
        while q and now - q[0] > LOGIN_WINDOW_S:
            q.popleft()
        return len(q) >= LOGIN_MAX_FAILS

    def fail(self, ip: str, now: Optional[float] = None) -> None:
        self._fails[ip].append(now or time.time())

    def reset(self, ip: str) -> None:
        self._fails.pop(ip, None)


def client_ip(request: Request) -> str:
    # nginx sets X-Real-IP; uvicorn listens on 127.0.0.1 only, so the header is trusted
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


def settings_dep(request: Request) -> StudioSettings:
    return request.app.state.settings


def require_user(request: Request, settings: StudioSettings = Depends(settings_dep)) -> str:
    user = read_session(settings.secret, request.cookies.get(COOKIE, ""))
    if not user or user != settings.admin_user:
        raise HTTPException(status_code=401, detail="not signed in")
    if request.method not in SAFE_METHODS and request.headers.get("x-studio") != "1":
        raise HTTPException(status_code=403, detail="missing X-Studio header")
    return user
