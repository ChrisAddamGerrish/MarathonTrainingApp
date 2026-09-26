"""Common FastAPI dependencies."""
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from backend.app.core import auth
from backend.app.core.database import get_session
from backend.app.core.errors import UnauthorizedError

SessionDep = Annotated[Session, Depends(get_session)]


def require_login(request: Request) -> str:
    """The signed-in user; refuses the request with 401 when there is no valid session cookie."""
    user = auth.session_user(request.cookies.get(auth.COOKIE))
    if user is None:
        raise UnauthorizedError("Sign in to continue.")
    return user
