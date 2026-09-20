"""Common FastAPI dependencies."""
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.core.database import get_session

SessionDep = Annotated[Session, Depends(get_session)]
