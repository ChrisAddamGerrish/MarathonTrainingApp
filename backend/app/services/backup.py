"""Reading the CSV backups the app exports (frontend/src/utils/csv.js) back into plain values.

Columns are found by their header, so their order doesn't matter and extra columns (Week, Pace,
Details... which the app works out itself) are ignored. Every row is validated with the same
rules as the forms; the first problem found is reported with its row number.
"""
import csv
import io
import re
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.app.core.errors import UnprocessableError
from backend.app.schemas import ActivityIn, PlanSessionIn, WeekType

# CSV header -> field, for each kind of backup.
WORKOUT_COLUMNS = {
    "Activity ID": "activity_id",
    "Date": "activity_date",
    "Category": "category",
    "Session": "actual_session",
    "Distance (mi)": "distance_mi",
    "Duration (min)": "duration_min",
    "Output (kJ)": "output_kj",
    "Plan ID": "plan_id",
    "Notes": "notes",
    "Details": "details",
}
PLAN_COLUMNS = {
    "Plan ID": "plan_id",
    "Week": "week",
    "Day": "day",
    "Week type": "week_type",
    "Category": "category",
    "Run type": "run_subtype",
    "Session": "planned_session",
    "Target distance (mi)": "target_distance_mi",
    "Target duration (min)": "target_duration_min",
    "Notes": "notes",
    "Skipped": "skipped",
    "Skip reason": "skip_reason",
}

# The export puts a ' before text a spreadsheet would treat as a formula; take it off again.
_FORMULA_GUARD = re.compile(r"^'(?=[=+\-@\t\r])")
# Exports made before the Activity ID column only have the details link (#/log/<id>).
_DETAILS_ID = re.compile(r"#/log/(\d+)$")


class WorkoutRow(ActivityIn):
    activity_id: Optional[int] = Field(None, ge=1)


class PlanRow(PlanSessionIn):
    plan_id: str = Field(min_length=1, max_length=50)
    week: int = Field(ge=1)
    week_type: WeekType
    skipped: bool = False
    skip_reason: Optional[str] = Field(None, max_length=200)

    @field_validator("skipped", mode="before")
    @classmethod
    def yes_no(cls, v):
        if v is None:  # a blank cell
            return False
        if isinstance(v, str):
            v = v.strip().lower()
            if v in ("", "no", "false", "0"):
                return False
            if v in ("yes", "true", "1"):
                return True
        return v


def _read(text: str, columns: dict[str, str], required: list[str]) -> list[tuple[int, dict[str, Any]]]:
    """(row number, {field: value or None}) for each non-blank data row."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = [h.strip() for h in next(reader, [])]
    missing = [h for h in required if h not in header]
    if missing:
        raise UnprocessableError(f"This file is missing the {', '.join(missing)} column{'s' if len(missing) > 1 else ''}. "
                                 "Is it the right kind of export?")
    index = {field: header.index(h) for h, field in columns.items() if h in header}
    rows = []
    for n, cells in enumerate(reader, start=2):  # row 1 is the header
        if not any(c.strip() for c in cells):
            continue
        values = {}
        for field, i in index.items():
            cell = _FORMULA_GUARD.sub("", cells[i].strip()) if i < len(cells) else ""
            values[field] = cell or None
        rows.append((n, values))
    return rows


def _validate(model: type[BaseModel], n: int, values: dict[str, Any]) -> dict[str, Any]:
    try:
        return model.model_validate(values).model_dump()
    except ValidationError as e:
        err = e.errors()[0]
        where = ".".join(str(x) for x in err["loc"])
        raise UnprocessableError(f"Row {n}: {where}: {err['msg']}") from e


def parse_workouts(text: str) -> list[dict[str, Any]]:
    rows = []
    for n, values in _read(text, WORKOUT_COLUMNS, ["Date", "Category", "Session"]):
        details = values.pop("details", None)
        if values.get("activity_id") is None and details and (m := _DETAILS_ID.search(details)):
            values["activity_id"] = m.group(1)
        rows.append(_validate(WorkoutRow, n, values))
    ids = [r["activity_id"] for r in rows if r["activity_id"] is not None]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise UnprocessableError(f"Activity ID {', '.join(map(str, dupes))} appears more than once")
    return rows


def parse_plan(text: str) -> list[dict[str, Any]]:
    rows = [_validate(PlanRow, n, values)
            for n, values in _read(text, PLAN_COLUMNS, ["Plan ID", "Week", "Day", "Week type", "Category", "Session"])]
    if not rows:
        raise UnprocessableError("The file has no planned sessions")
    ids = [r["plan_id"] for r in rows]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise UnprocessableError(f"Plan ID {', '.join(dupes)} appears more than once")
    week_types: dict[int, str] = {}
    for r in rows:
        if week_types.setdefault(r["week"], r["week_type"]) != r["week_type"]:
            raise UnprocessableError(f"Week {r['week']} has more than one week type")
    for r in rows:
        if r["skipped"] and r["category"] == "Rest":
            raise UnprocessableError(f"{r['plan_id']} is a rest day, which can't be skipped")
        if not r["skipped"]:
            r["skip_reason"] = None
    return rows
