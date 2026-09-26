"""Sign-in routes. The session lives in an HttpOnly cookie, so the front end never handles a token."""
import logging
import threading
import time

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from backend.app.core import auth
from backend.app.core.errors import NotConfiguredError, TooManyAttemptsError, UnauthorizedError

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("marathon.auth")

# Slow down password guessing: after MAX_FAILURES wrong attempts from one address within WINDOW
# seconds, refuse further attempts from it until the oldest one ages out.
MAX_FAILURES = 5
WINDOW = 15 * 60
_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


class LoginIn(BaseModel):
    username: str
    password: str
    remember: bool = False


def _client(request: Request) -> str:
    # Behind Caddy, uvicorn's proxy-header support has already replaced this with the real client.
    return request.client.host if request.client else "unknown"


def _recent_failures(client: str) -> list[float]:
    cutoff = time.time() - WINDOW
    recent = [t for t in _failures.get(client, []) if t > cutoff]
    _failures[client] = recent
    return recent


@router.get("/session")
def session_status(request: Request):
    user = auth.session_user(request.cookies.get(auth.COOKIE))
    return {"authenticated": user is not None, "user": user, "configured": auth.load_credentials() is not None}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    creds = auth.load_credentials()
    if creds is None:
        raise NotConfiguredError("Sign-in isn't set up yet. On the server, run start.ps1 -ResetLogin.")
    client = _client(request)
    with _lock:
        if len(_recent_failures(client)) >= MAX_FAILURES:
            raise TooManyAttemptsError("Too many failed sign-in attempts. Try again in 15 minutes.")
    if not auth.check_login(body.username, body.password, creds):
        with _lock:
            _recent_failures(client).append(time.time())
        raise UnauthorizedError("Wrong username or password.")
    with _lock:
        _failures.pop(client, None)
    log.info("%s signed in from %s", creds.user, client)
    lifetime = auth.REMEMBER_SECONDS if body.remember else auth.SESSION_SECONDS
    # No max_age without "remember me": a session cookie, gone when the browser closes.
    response.set_cookie(auth.COOKIE, auth.create_session(creds, lifetime), path="/",
                        max_age=auth.REMEMBER_SECONDS if body.remember else None,
                        httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return {"authenticated": True, "user": creds.user, "configured": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"authenticated": False, "user": None, "configured": True}
