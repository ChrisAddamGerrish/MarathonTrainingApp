"""Audit history routes."""
from typing import Annotated, Optional

from fastapi import APIRouter, Query

from backend.app.api.deps import SessionDep
from backend.app.repository.repository import history_entries, revert_history

router = APIRouter(prefix="/history", tags=["history"])


@router.get("")
def get_history(
    session: SessionDep,
    activity_id: Optional[int] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    return history_entries(session, activity_id, limit)


@router.post("/{history_id}/revert")
def revert_history_entry(history_id: int, session: SessionDep):
    return revert_history(session, history_id)
