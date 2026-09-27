"""Common FastAPI dependencies: who is signed in, and a database session scoped to them."""
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Iterator, Optional

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from backend.app.core import auth
from backend.app.core.database import SessionLocal, Tenant, user_session
from backend.app.core.errors import ForbiddenError, UnauthorizedError
from backend.app.models import User
from backend.app.services import strava


@dataclass(frozen=True)
class SignedIn:
    user_id: int
    username: str
    is_admin: bool
    plan_start: date

    @property
    def tenant(self) -> Tenant:
        return Tenant(self.user_id, self.plan_start)


def signed_in_user(request: Request) -> Optional[SignedIn]:
    """The user the request's session cookie belongs to, or None."""
    with SessionLocal() as session:
        user_id = auth.read_session(request.cookies.get(auth.COOKIE),
                                    lambda uid: (u := session.get(User, uid)) and u.password_hash)
        user = session.get(User, user_id) if user_id is not None else None
        return SignedIn(user.user_id, user.username, user.is_admin, user.plan_start) if user else None


def require_login(request: Request) -> SignedIn:
    """The signed-in user; refuses the request with 401 when there is no valid session cookie."""
    user = signed_in_user(request)
    if user is None:
        raise UnauthorizedError("Sign in to continue.")
    return user


UserDep = Annotated[SignedIn, Depends(require_login)]


def require_admin(user: UserDep) -> SignedIn:
    if not user.is_admin:
        raise ForbiddenError("Only an admin can do that.")
    return user


AdminDep = Annotated[SignedIn, Depends(require_admin)]


def get_user_session(user: UserDep) -> Iterator[Session]:
    """A session over the signed-in user's own data. Their account has to be connected to Strava
    first (the last step of registering; see services/accounts.py)."""
    if not strava.is_connected(user.user_id):
        raise ForbiddenError("Connect your Strava account to finish setting up.")
    with user_session(user.tenant) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_user_session)]
