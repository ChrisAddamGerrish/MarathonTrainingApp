"""Restoring the CSV backups the app exports. The file is the request body (text/csv).

Without ?apply=true nothing is written: the response says what the restore would change, so the
app can show that before you confirm.
"""
from typing import Annotated

from fastapi import APIRouter, Body

from backend.app.api.deps import SessionDep
from backend.app.repository.repository import restore_activities, restore_plan
from backend.app.services.backup import parse_plan, parse_workouts

router = APIRouter(prefix="/restore", tags=["backup"])

CsvBody = Annotated[str, Body(media_type="text/csv")]


@router.post("/workouts")
def restore_workouts_backup(csv: CsvBody, session: SessionDep, apply: bool = False):
    return restore_activities(session, parse_workouts(csv), apply)


@router.post("/plan")
def restore_plan_backup(csv: CsvBody, session: SessionDep, apply: bool = False):
    return restore_plan(session, parse_plan(csv), apply)
