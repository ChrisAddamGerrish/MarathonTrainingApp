"""Activity log routes."""
from fastapi import APIRouter

from backend.app.api.deps import SessionDep
from backend.app.repository.repository import create_activity, delete_activity, update_activity
from backend.app.schemas.schemas import ActivityIn

router = APIRouter(prefix="/activities", tags=["activities"])


@router.post("", status_code=201)
def create_new_activity(body: ActivityIn, session: SessionDep):
    return create_activity(session, body.model_dump())


@router.put("/{activity_id}")
def update_existing_activity(activity_id: int, body: ActivityIn, session: SessionDep):
    return update_activity(session, activity_id, body.model_dump())


@router.delete("/{activity_id}")
def delete_existing_activity(activity_id: int, session: SessionDep):
    return delete_activity(session, activity_id)
