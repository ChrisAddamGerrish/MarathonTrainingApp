"""Sign-in for the web app: users with scrypt password hashes, and signed session cookies.

Accounts live in the users table (models.User); new people register with an invite code an admin
hands out (services/accounts.py). config.AUTH_FILE (auth.env, git-ignored) now only holds
MARATHON_SECRET_KEY, which signs session cookies. (Its old MARATHON_USER / MARATHON_PASSWORD_HASH
lines became user 1 when the database was migrated; migrations.migrate_to_multi_user.)

Passwords are set and invites made from the command line: see backend/cli.py.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Optional

from backend.app.core import config

COOKIE = "marathon_session"
# "Remember me" keeps a device signed in for REMEMBER_SECONDS. Without it the cookie ends with the
# browser session, and the server stops honouring it after SESSION_SECONDS in case the browser
# restores it.
REMEMBER_SECONDS = 90 * 24 * 3600
SESSION_SECONDS = 12 * 3600
MIN_PASSWORD_LENGTH = 12
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,40}$")

# scrypt cost: 16 MiB of memory and ~50 ms per check. Stored with the hash so it can be raised later.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected))
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


# Checked against when the username doesn't exist, so that takes as long as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def check_password(password: str, stored: Optional[str]) -> bool:
    """Whether `password` matches; always does the (slow) check, even for a missing account."""
    ok = verify_password(password, stored or _DUMMY_HASH)
    return ok and bool(stored)


# --------------------------------------------------------------------------
# The cookie-signing key (auth.env)
# --------------------------------------------------------------------------


def secret_key() -> str:
    """MARATHON_SECRET_KEY from auth.env, created (and saved) the first time it's needed."""
    values = config.read_env_file(config.AUTH_FILE)
    if values.get("MARATHON_SECRET_KEY"):
        return values["MARATHON_SECRET_KEY"]
    key = secrets.token_urlsafe(32)
    lines = config.AUTH_FILE.read_text(encoding="utf-8").splitlines() if config.AUTH_FILE.exists() else [
        "# Signs the web app's session cookies. Keep it private; changing it signs everyone out."]
    config.AUTH_FILE.write_text("\n".join(lines + [f"MARATHON_SECRET_KEY={key}"]) + "\n", encoding="utf-8")
    return key


# --------------------------------------------------------------------------
# Session cookies: base64(JSON payload) + "." + HMAC. The key mixes in the user's password hash,
# so changing a password invalidates every session that user had.
# --------------------------------------------------------------------------


def _signature(payload: str, password_hash: str) -> str:
    key = hashlib.sha256(f"{secret_key()}:{password_hash}".encode()).digest()
    return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def create_session(user_id: int, password_hash: str, lifetime: int = SESSION_SECONDS) -> str:
    payload = _b64(json.dumps({"uid": user_id, "exp": int(time.time()) + lifetime}).encode())
    return f"{payload}.{_signature(payload, password_hash)}"


def read_session(token: Optional[str], password_hash_of) -> Optional[int]:
    """The user id a session cookie was issued to, or None if it is missing, forged or expired.
    `password_hash_of(user_id)` returns that user's current hash (None if there's no such user)."""
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    try:
        data = json.loads(_unb64(payload))
        user_id = int(data["uid"])
    except (ValueError, KeyError, TypeError):
        return None
    stored = password_hash_of(user_id)
    if not stored or not hmac.compare_digest(signature, _signature(payload, stored)):
        return None
    return user_id if data.get("exp", 0) >= time.time() else None
