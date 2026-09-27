"""User accounts: registering with an invite code, invites, passwords.

A new account starts with an empty plan: `weeks` Normal weeks from its plan start (a Monday) and no
sessions, to be filled in on the Plan page or from a plan backup. Registering doesn't finish the
setup: the account has to connect Strava before it can use the app (api/deps.py).
"""
import hashlib
import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core import auth
from backend.app.core.database import SessionLocal
from backend.app.core.errors import ConflictError, UnprocessableError
from backend.app.models.models import Invite, PlanWeek, User

log = logging.getLogger("marathon.auth")

INVITE_DAYS = 7
DEFAULT_WEEKS = 20
MAX_WEEKS = 52


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


def find_user(session: Session, username: str) -> Optional[User]:
    return session.scalars(select(User).where(User.username == username.strip())).first()  # NOCASE column


def any_can_sign_in(session: Session) -> bool:
    return session.scalar(select(User.user_id).where(User.password_hash != "").limit(1)) is not None


def _add_user(session: Session, username: str, password: str, plan_start: date, weeks: int,
              is_admin: bool = False) -> User:
    user = User(username=username, password_hash=auth.hash_password(password), is_admin=is_admin,
                plan_start=plan_start)
    session.add(user)
    session.flush()  # the user row first: the weeks refer to it
    session.add_all(PlanWeek(user_id=user.user_id, week=n, week_type="Normal") for n in range(1, weeks + 1))
    return user


def validate_new_account(session: Session, username: str, password: str, plan_start: date, weeks: int) -> None:
    if not auth.USERNAME_PATTERN.match(username):
        raise UnprocessableError("Usernames are up to 40 letters, digits, dots, dashes and underscores.")
    if len(password) < auth.MIN_PASSWORD_LENGTH:
        raise UnprocessableError(f"The password must be at least {auth.MIN_PASSWORD_LENGTH} characters.")
    if plan_start.weekday() != 0:
        raise UnprocessableError("The plan has to start on a Monday.")
    if not 1 <= weeks <= MAX_WEEKS:
        raise UnprocessableError(f"A plan can have 1 to {MAX_WEEKS} weeks.")
    if find_user(session, username) is not None:
        raise ConflictError("That username is taken.")


def register(session: Session, invite_code: str, username: str, password: str, plan_start: date,
             weeks: int) -> User:
    """Create an account with a (single-use) invite code. Raises a domain error if anything's off."""
    invite = session.get(Invite, _code_hash(invite_code))
    now = _utc(datetime.now(timezone.utc))
    if invite is None or invite.used_by is not None or invite.expires_at < now:
        raise UnprocessableError("That invite code isn't valid. It may have been used already or expired.")
    validate_new_account(session, username, password, plan_start, weeks)
    user = _add_user(session, username, password, plan_start, weeks)
    invite.used_by, invite.used_at = user.user_id, now
    session.commit()
    log.info("User %s (%s) registered; plan starts %s, %s weeks", user.user_id, username, plan_start, weeks)
    return user


def create_invite(created_by: Optional[int]) -> tuple[str, str]:
    """A new invite code and when it expires (UTC). created_by None: the first admin."""
    code = secrets.token_urlsafe(9)  # 12 characters
    expires = _utc(datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS))
    with SessionLocal() as session:
        if created_by is None:
            created_by = session.scalar(select(User.user_id).where(User.is_admin).order_by(User.user_id).limit(1))
            if created_by is None:
                raise ConflictError("There's no admin account yet. Create one first (python -m backend.app.core.auth).")
        session.add(Invite(code_hash=_code_hash(code), created_by=created_by, expires_at=expires))
        session.commit()
    log.info("Invite created by user %s, expires %s", created_by, expires)
    return code, expires


def set_password(username: str, password: str, plan_start: date) -> bool:
    """Change a user's password, or create them as an admin (with an empty plan) if they don't exist.
    Returns whether the account was created."""
    with SessionLocal() as session:
        user = find_user(session, username)
        if user is None:
            _add_user(session, username, password, plan_start, DEFAULT_WEEKS, is_admin=True)
        else:
            user.password_hash = auth.hash_password(password)
        session.commit()
    log.info("%s for %s from the command line", "Admin account created" if user is None else "Password changed",
             username)
    return user is None
