"""Request bodies (validated by Pydantic)."""
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# Match models.ACTIVITY_CATEGORIES / PLAN_CATEGORIES: a logged activity can be a Stretch, a
# planned session can't. The rest match the CHECK constraints on training_plan.
Category = Literal["Strength", "Bike", "Run", "Race", "Row", "Rest", "Stretch"]
PlanCategory = Literal["Strength", "Bike", "Run", "Race", "Row", "Rest"]
Day = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WeekType = Literal["Normal", "Step-back", "Taper", "Peak", "Race"]
RunSubtype = Literal["Easy", "Tempo", "Intervals", "Hills", "Strides", "Race-Pace Segments", "Long Run"]


class ActivityIn(BaseModel):
    activity_date: date
    category: Category
    actual_session: str = Field(min_length=1)
    distance_mi: Optional[float] = Field(None, ge=0)
    duration_min: Optional[float] = Field(None, ge=0)
    output_kj: Optional[float] = Field(None, ge=0)
    plan_id: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("actual_session", "plan_id", "notes", mode="before")
    @classmethod
    def strip_text(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("plan_id", "notes", mode="after")
    @classmethod
    def blank_to_none(cls, v):
        return v or None


class SkipIn(BaseModel):
    reason: Optional[str] = Field(None, max_length=200)

    @field_validator("reason", mode="before")
    @classmethod
    def clean_reason(cls, v):
        v = v.strip() if isinstance(v, str) else v
        return v or None


class PlanSessionIn(BaseModel):
    """A planned session as the plan editor sends it (the week comes from the URL or stays put)."""

    day: Day
    category: PlanCategory
    run_subtype: Optional[RunSubtype] = None
    planned_session: str = Field(min_length=1, max_length=200)
    target_distance_mi: Optional[float] = Field(None, ge=0)
    target_duration_min: Optional[float] = Field(None, ge=0)
    notes: Optional[str] = None

    @field_validator("planned_session", "notes", mode="before")
    @classmethod
    def strip_text(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("notes", mode="after")
    @classmethod
    def blank_to_none(cls, v):
        return v or None

    @model_validator(mode="after")
    def only_runs_have_a_run_type(self):
        if self.category not in ("Run", "Race"):
            self.run_subtype = None
        if self.category == "Rest":
            self.target_distance_mi = self.target_duration_min = None
        return self


class WeekIn(BaseModel):
    week_type: WeekType
