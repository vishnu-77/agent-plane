"""Credential handling: passwords, API keys, and console sessions.

Three separate concerns, deliberately kept apart:

* **Passwords** are salted and stretched with scrypt (stdlib, no new
  dependency). Only the derived hash is stored.
* **API keys** are high-entropy secrets, so a keyed HMAC-SHA256 is the right
  construction: it is constant-time comparable, indexable for O(1) lookup, and
  useless to an attacker who only reads the database without the server key.
  The plaintext is shown exactly once, at creation.
* **Console sessions** are signed cookies: ``base64(payload).hmac``. No server
  session table, no token in local storage, and an expiry inside the payload.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Literal

Environment = Literal["live", "test", "mgmt"]
KEY_PREFIX: dict[str, str] = {"live": "ap_live_", "test": "ap_test_", "mgmt": "ap_mgmt_"}
SECRET_BYTES = 24          # 32 url-safe characters
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


# --------------------------------------------------------------------------- #
# ids
# --------------------------------------------------------------------------- #
def new_id(prefix: str, length: int = 16) -> str:
    """A readable, sortable-enough public id: ``prj_9f2c1a...``."""
    return f"{prefix}_{secrets.token_hex(length // 2)}"


# --------------------------------------------------------------------------- #
# passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT["n"], _SCRYPT["r"], _SCRYPT["p"],
        base64.b64encode(salt).decode(), base64.b64encode(derived).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p), dklen=len(base64.b64decode(hash_b64)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, base64.b64decode(hash_b64))


def password_problems(password: str) -> list[str]:
    problems: list[str] = []
    if len(password) < 10:
        problems.append("must be at least 10 characters")
    if password.lower() in {"password12", "agentplane", "1234567890"}:
        problems.append("is too common")
    return problems


# --------------------------------------------------------------------------- #
# API keys
# --------------------------------------------------------------------------- #
def generate_api_key(environment: Environment = "live") -> tuple[str, str, str]:
    """Return ``(plaintext, prefix, last4)``. The plaintext is never stored."""
    secret = secrets.token_urlsafe(SECRET_BYTES)
    prefix = KEY_PREFIX[environment]
    return prefix + secret, prefix, secret[-4:]


def hash_api_key(plaintext: str, server_secret: str) -> str:
    return hmac.new(server_secret.encode("utf-8"), plaintext.encode("utf-8"), hashlib.sha256).hexdigest()


def masked_key(prefix: str, last4: str) -> str:
    return f"{prefix}{'•' * 12}{last4}"


def looks_like_api_key(value: str) -> bool:
    return any(value.startswith(p) for p in KEY_PREFIX.values())


def environment_of(value: str) -> Environment | None:
    for env, prefix in KEY_PREFIX.items():
        if value.startswith(prefix):
            return env  # type: ignore[return-value]
    return None


# --------------------------------------------------------------------------- #
# console sessions
# --------------------------------------------------------------------------- #
SESSION_COOKIE = "ap_session"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64u(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_session(payload: dict[str, Any], secret: str, ttl_seconds: int) -> str:
    body = {**payload, "exp": int(time.time()) + ttl_seconds}
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    mac = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_b64u(raw)}.{_b64u(mac)}"


def read_session(cookie: str | None, secret: str) -> dict[str, Any] | None:
    if not cookie or "." not in cookie:
        return None
    body_b64, mac_b64 = cookie.rsplit(".", 1)
    try:
        raw, mac = _unb64u(body_b64), _unb64u(mac_b64)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) < time.time():
        return None
    return payload
