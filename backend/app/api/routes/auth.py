"""Sign-in and registration routes. The session lives in an HttpOnly cookie, so the front end never
handles a token."""
import logging
import threading
import time
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel

from backend.app.api.deps import AdminDep, signed_in_user
from backend.app.core import auth
from backend.app.core.database import SessionLocal
from backend.app.core.errors import AppError, NotConfiguredError, TooManyAttemptsError, UnauthorizedError
from backend.app.schemas import PlanGoalIn
from backend.app.services import accounts, generator, strava

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("marathon.auth")

# Slow down guessing (passwords and invite codes): after MAX_FAILURES failed attempts from one
# address within WINDOW seconds, refuse further attempts from it until the oldest one ages out.
MAX_FAILURES = 5
WINDOW = 15 * 60
_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


class LoginIn(BaseModel):
    username: str
    password: str
    remember: bool = False


class RegisterIn(BaseModel):
    invite: str
    username: str
    password: str
    plan_start: date
    weeks: int = accounts.DEFAULT_WEEKS
    goal: Optional[PlanGoalIn] = None  # None: the plan starts empty
    remember: bool = False


def _client(request: Request) -> str:
    # Behind Caddy, uvicorn's proxy-header support has already replaced this with the real client.
    return request.client.host if request.client else "unknown"


def _recent_failures(client: str) -> list[float]:
    cutoff = time.time() - WINDOW
    recent = [t for t in _failures.get(client, []) if t > cutoff]
    _failures[client] = recent
    return recent


def _check_not_locked_out(client: str, what: str) -> None:
    with _lock:
        if len(_recent_failures(client)) >= MAX_FAILURES:
            raise TooManyAttemptsError(f"Too many failed {what} attempts. Try again in 15 minutes.")


def _failed(client: str) -> None:
    with _lock:
        _recent_failures(client).append(time.time())


def _session_info(user) -> dict:
    if user is None:
        with SessionLocal() as session:
            configured = accounts.any_can_sign_in(session)
        return {"authenticated": False, "user": None, "configured": configured}
    return {"authenticated": True, "user": user.username, "configured": True, "is_admin": user.is_admin,
            "strava_connected": strava.is_connected(user.user_id)}


def _set_cookie(request: Request, response: Response, user_id: int, password_hash: str, remember: bool) -> None:
    lifetime = auth.REMEMBER_SECONDS if remember else auth.SESSION_SECONDS
    # No max_age without "remember me": a session cookie, gone when the browser closes.
    response.set_cookie(auth.COOKIE, auth.create_session(user_id, password_hash, lifetime), path="/",
                        max_age=auth.REMEMBER_SECONDS if remember else None,
                        httponly=True, samesite="lax", secure=request.url.scheme == "https")


@router.get("/session")
def session_status(request: Request):
    return _session_info(signed_in_user(request))


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    client = _client(request)
    _check_not_locked_out(client, "sign-in")
    with SessionLocal() as session:
        if not accounts.any_can_sign_in(session):
            raise NotConfiguredError("Sign-in isn't set up yet. On the server, create the first account: run "
                                     "start.ps1 -ResetLogin, or with Docker "
                                     "docker compose exec app python -m backend.cli.")
        user = accounts.find_user(session, body.username)
        if not auth.check_password(body.password, user.password_hash if user else None):
            _failed(client)
            raise UnauthorizedError("Wrong username or password.")
    with _lock:
        _failures.pop(client, None)
    log.info("%s signed in from %s", user.username, client)
    _set_cookie(request, response, user.user_id, user.password_hash, body.remember)
    return _session_info(user)


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response):
    """Create an account with an invite code and sign it in. Connecting Strava comes next."""
    client = _client(request)
    _check_not_locked_out(client, "registration")
    with SessionLocal() as session:
        try:
            user = accounts.register(session, body.invite, body.username.strip(), body.password,
                                     body.plan_start, body.weeks,
                                     body.goal.model_dump() if body.goal else None)
        except AppError:
            _failed(client)  # includes wrong invite codes, which is what this is here to slow down
            raise
    log.info("%s registered from %s", user.username, client)
    _set_cookie(request, response, user.user_id, user.password_hash, body.remember)
    return _session_info(user)


@router.get("/plan-minimum")
def plan_minimum(longest_run_mi: Annotated[float, Query(ge=0, le=50)]):
    """The shortest plan (in weeks) a new runner can generate from their longest recent run, so the
    registration form can say so before it's submitted."""
    return {"min_weeks": generator.min_weeks(longest_run_mi), "min_long_run_mi": generator.MIN_LONG_RUN_MI,
            "min_weekly_mi": generator.MIN_WEEKLY_MI, "runs_per_week": sorted(generator.RUN_DAYS)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"authenticated": False, "user": None, "configured": True}


@router.post("/invites", status_code=201)
def create_invite(admin: AdminDep):
    code, expires = accounts.create_invite(admin.user_id)
    return {"code": code, "expires_at": expires}
