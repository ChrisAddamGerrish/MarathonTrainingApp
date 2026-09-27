"""Training-plan rules: calendar dates, session status, weekly totals, summary numbers.

Pure functions over plain dicts (no database access), so they are easy to reason about and test.
"""
from datetime import date, timedelta

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
RUN_CATEGORIES = ("Run", "Race")


def week_of(d: date, plan_start: date) -> int:
    """1-based plan week for a calendar date (<=0 before the plan, past the end after it).
    plan_start is the Monday of week 1 (each user has their own)."""
    return (d - plan_start).days // 7 + 1


def plan_date(week: int, day: str, plan_start: date) -> date:
    return plan_start + timedelta(weeks=week - 1, days=DAYS.index(day))


def plan_status(row: dict, today: date) -> str:
    if row["category"] == "Rest":
        return "rest"
    if row["linked_activity_count"]:
        return "done"
    if row["skipped"]:
        return "skipped"
    if row["optional"]:
        return "optional"
    d = date.fromisoformat(row["date"])
    if d < today:
        return "missed"
    return "today" if d == today else "upcoming"


def build_plan(rows: list[dict], today: date, plan_start: date) -> list[dict]:
    """Add calendar date, status and adherence flags to the plan rows loaded from the database."""
    plan = []
    for r in rows:
        item = dict(r)
        item["date"] = plan_date(item["week"], item["day"], plan_start).isoformat()
        item["optional"] = item["planned_session"].startswith("Optional")
        item["status"] = plan_status(item, today)
        # Rest days, optional slots and skipped sessions never count for/against adherence.
        item["counts"] = item["category"] != "Rest" and not item["optional"] and item["status"] != "skipped"
        plan.append(item)
    # Stable order within a week: by weekday, keeping the table's own row order.
    plan.sort(key=lambda p: (p["week"], DAYS.index(p["day"])))
    return plan


def build_weeks(plan_weeks: list[dict], plan: list[dict], activities: list[dict], plan_start: date) -> list[dict]:
    """Totals for every week of the plan ({week, week_type} rows), including weeks with no sessions yet."""
    weeks: dict[int, dict] = {
        pw["week"]: {
            "week": pw["week"],
            "week_type": pw["week_type"],
            "start": (plan_start + timedelta(weeks=pw["week"] - 1)).isoformat(),
            "end": (plan_start + timedelta(weeks=pw["week"] - 1, days=6)).isoformat(),
            "planned_run_mi": 0.0,
            "actual_run_mi": 0.0,
            "actual_bike_mi": 0.0,
            "planned_min": 0.0,
            "actual_min": 0.0,
            "sessions_planned": 0,
            "sessions_done": 0,
            # Minutes in each heart-rate zone (Z1..Z5), from workouts whose Strava heart-rate data was read.
            "hr_zone_min": [],
        }
        for pw in plan_weeks
    }
    for p in plan:
        w = weeks[p["week"]]
        if p["category"] in RUN_CATEGORIES:
            w["planned_run_mi"] += p["target_distance_mi"] or 0
        w["planned_min"] += p["target_duration_min"] or 0
        if p["counts"]:
            w["sessions_planned"] += 1
            w["sessions_done"] += 1 if p["status"] == "done" else 0
    # Actual volume follows the calendar date, so unplanned add-ons still count.
    for a in activities:
        w = weeks.get(a["week"])
        if not w:
            continue
        if a["category"] in RUN_CATEGORIES:
            w["actual_run_mi"] += a["distance_mi"] or 0
        elif a["category"] == "Bike":
            w["actual_bike_mi"] += a["distance_mi"] or 0
        w["actual_min"] += a["duration_min"] or 0
        zones = (a.get("strava") or {}).get("hr_zone_seconds") or []
        if len(w["hr_zone_min"]) < len(zones):
            w["hr_zone_min"] += [0.0] * (len(zones) - len(w["hr_zone_min"]))
        for n, seconds in enumerate(zones):
            w["hr_zone_min"][n] += seconds / 60
    result = [weeks[k] for k in sorted(weeks)]
    for w in result:
        for key in ("planned_run_mi", "actual_run_mi", "actual_bike_mi", "planned_min", "actual_min"):
            w[key] = round(w[key], 2)
        w["hr_zone_min"] = [round(m, 1) for m in w["hr_zone_min"]]
    return result


def build_summary(plan: list[dict], activities: list[dict], weeks: list[dict], today: date, plan_start: date) -> dict:
    last_week = max(w["week"] for w in weeks)
    race_date = plan_date(last_week, "Sun", plan_start)
    due = [p for p in plan if p["counts"] and p["status"] in ("done", "missed")]
    return {
        "plan_start": plan_start.isoformat(),
        "race_date": race_date.isoformat(),
        "today": today.isoformat(),
        "days_to_race": (race_date - today).days,
        "current_week": week_of(today, plan_start),
        "last_week": last_week,
        "total_run_mi": round(sum(a["distance_mi"] or 0 for a in activities if a["category"] in RUN_CATEGORIES), 2),
        "planned_run_mi_to_date": round(
            sum(p["target_distance_mi"] or 0 for p in plan
                if p["category"] in RUN_CATEGORIES and p["status"] != "skipped"
                and date.fromisoformat(p["date"]) <= today), 2),
        "sessions_due": len(due),
        "sessions_done": sum(1 for p in due if p["status"] == "done"),
        "sessions_skipped": sum(1 for p in plan if p["status"] == "skipped" and not p["optional"]),
        "total_minutes": round(sum(a["duration_min"] or 0 for a in activities), 1),
        "activity_count": len(activities),
    }
