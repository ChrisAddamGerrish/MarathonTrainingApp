"""Sign-in for the web app: one user, a scrypt password hash and signed session cookies.

The login lives in config.AUTH_FILE (auth.env, git-ignored) as KEY=VALUE lines:
  MARATHON_USER, MARATHON_PASSWORD_HASH (scrypt, never the password) and MARATHON_SECRET_KEY
  (signs session cookies). Set it up or change it with:

    .venv\\Scripts\\python.exe -m backend.app.core.auth

start.ps1 runs that on first start and with -ResetLogin. The file is re-read when it changes, so a
new password applies without restarting, and signs out every existing session.
"""
import base64
import getpass
import hashlib
import hmac
import json
import re
import secrets
import sys
import time
from dataclasses import dataclass
from typing import Optional

from backend.app.core import config

COOKIE = "marathon_session"
# "Remember me" keeps a device signed in for REMEMBER_SECONDS. Without it the cookie ends with the
# browser session, and the server stops honouring it after SESSION_SECONDS in case the browser
# restores it.
REMEMBER_SECONDS = 90 * 24 * 3600
SESSION_SECONDS = 12 * 3600
MIN_PASSWORD_LENGTH = 12
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

# scrypt cost: 16 MiB of memory and ~50 ms per check. Stored with the hash so it can be raised later.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


@dataclass(frozen=True)
class Credentials:
    user: str
    password_hash: str
    secret: str


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


# --------------------------------------------------------------------------
# The login file
# --------------------------------------------------------------------------

_cache: tuple[Optional[float], Optional[Credentials]] = (None, None)


def _read_file() -> dict[str, str]:
    values = {}
    for line in config.AUTH_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def load_credentials() -> Optional[Credentials]:
    """The configured login, or None if the login file is missing or incomplete."""
    global _cache
    try:
        mtime = config.AUTH_FILE.stat().st_mtime
    except OSError:
        return None
    if _cache[0] != mtime:
        values = _read_file()
        user, password_hash, secret = (values.get(k) for k in
                                       ("MARATHON_USER", "MARATHON_PASSWORD_HASH", "MARATHON_SECRET_KEY"))
        _cache = (mtime, Credentials(user, password_hash, secret) if user and password_hash and secret else None)
    return _cache[1]


def check_login(username: str, password: str, creds: Credentials) -> bool:
    # Always run the (slow) password check, so a wrong username takes as long as a wrong password.
    password_ok = verify_password(password, creds.password_hash)
    return hmac.compare_digest(username.encode(), creds.user.encode()) and password_ok


# --------------------------------------------------------------------------
# Session cookies: base64(JSON payload) + "." + HMAC. The key mixes in the password hash, so
# changing the password invalidates every session issued before.
# --------------------------------------------------------------------------


def _signature(payload: str, creds: Credentials) -> str:
    key = hashlib.sha256(f"{creds.secret}:{creds.password_hash}".encode()).digest()
    return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def create_session(creds: Credentials, lifetime: int = SESSION_SECONDS) -> str:
    payload = _b64(json.dumps({"u": creds.user, "exp": int(time.time()) + lifetime}).encode())
    return f"{payload}.{_signature(payload, creds)}"


def session_user(token: Optional[str]) -> Optional[str]:
    """The signed-in user for a session cookie value, or None if it is missing, forged or expired."""
    creds = load_credentials()
    if not token or creds is None or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(signature, _signature(payload, creds)):
        return None
    try:
        data = json.loads(_unb64(payload))
    except ValueError:
        return None
    if data.get("u") != creds.user or data.get("exp", 0) < time.time():
        return None
    return creds.user


# --------------------------------------------------------------------------
# Setup: python -m backend.app.core.auth
# --------------------------------------------------------------------------


def write_credentials(user: str, password: str) -> None:
    """Save a login, keeping the existing secret key (or making one) and any other lines."""
    lines, secret = [], None
    if config.AUTH_FILE.exists():
        for line in config.AUTH_FILE.read_text(encoding="utf-8").splitlines():
            key = line.split("=", 1)[0].strip()
            if key == "MARATHON_SECRET_KEY":
                secret = line.split("=", 1)[1].strip()
            elif key not in ("MARATHON_USER", "MARATHON_PASSWORD_HASH"):
                lines.append(line)
    if not lines:
        lines = ["# Web app login, written by `python -m backend.app.core.auth`. Keep it private."]
    lines += [f"MARATHON_USER={user}", f"MARATHON_PASSWORD_HASH={hash_password(password)}",
              f"MARATHON_SECRET_KEY={secret or secrets.token_urlsafe(32)}"]
    config.AUTH_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    current = load_credentials()
    prompt = f"Username (Enter keeps '{current.user}'): " if current else "Username: "
    user = input(prompt).strip() or (current.user if current else "")
    if not USERNAME_PATTERN.match(user):
        print("The username may only contain letters, digits, dot, dash and underscore.", file=sys.stderr)
        return 1
    password = getpass.getpass(f"Password ({MIN_PASSWORD_LENGTH}+ characters): ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"The password must be at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Password again: ") != password:
        print("The passwords did not match.", file=sys.stderr)
        return 1
    write_credentials(user, password)
    print(f"Login saved to {config.AUTH_FILE}. Existing sessions are signed out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
