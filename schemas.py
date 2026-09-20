"""Request bodies (validated by Pydantic)."""
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Category = Literal["Strength", "Bike", "Run", "Race", "Row", "Rest"]


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
