"""Request bodies (validated by Pydantic)."""
from datetime import date
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, BeforeValidator, Field, model_validator

# Match models.ACTIVITY_CATEGORIES / PLAN_CATEGORIES: a logged activity can be a Stretch, a
# planned session can't. The rest match the CHECK constraints on training_plan.
Category = Literal["Strength", "Bike", "Run", "Race", "Row", "Rest", "Stretch"]
PlanCategory = Literal["Strength", "Bike", "Run", "Race", "Row", "Rest"]
Day = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WeekType = Literal["Normal", "Step-back", "Taper", "Peak", "Race"]
RunSubtype = Literal["Easy", "Tempo", "Intervals", "Hills", "Strides", "Race-Pace Segments", "Long Run"]

# Text fields arrive with surrounding spaces trimmed; optional ones left blank become None.
Text = Annotated[str, BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v)]
OptionalText = Annotated[Optional[Text], AfterValidator(lambda v: v or None)]


class ActivityIn(BaseModel):
    activity_date: date
    category: Category
    actual_session: Text = Field(min_length=1)
    distance_mi: Optional[float] = Field(None, ge=0)
    duration_min: Optional[float] = Field(None, ge=0)
    output_kj: Optional[float] = Field(None, ge=0)
    plan_id: OptionalText = None
    notes: OptionalText = None


class SkipIn(BaseModel):
    reason: OptionalText = Field(None, max_length=200)


class PlanSessionIn(BaseModel):
    """A planned session as the plan editor sends it (the week comes from the URL or stays put)."""

    day: Day
    category: PlanCategory
    run_subtype: Optional[RunSubtype] = None
    planned_session: Text = Field(min_length=1, max_length=200)
    target_distance_mi: Optional[float] = Field(None, ge=0)
    target_duration_min: Optional[float] = Field(None, ge=0)
    notes: OptionalText = None

    @model_validator(mode="after")
    def only_runs_have_a_run_type(self):
        if self.category not in ("Run", "Race"):
            self.run_subtype = None
        if self.category == "Rest":
            self.target_distance_mi = self.target_duration_min = None
        return self


class WeekIn(BaseModel):
    week_type: WeekType
