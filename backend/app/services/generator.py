"""Generating a marathon plan for a new account from where the runner is now.

Plans are built backward from race day, like most published plans: race week, a two-week taper,
the peak long run (20 mi) three weeks out, and before that a build where the long run grows by a
mile a week with an easier step-back week every fourth week. A long plan holds the long run at a
base level (BASE_LONG_RUN_MI) until the build starts, rather than peaking months early.

Safety rules: min_weeks is how long it takes to get from the runner's current longest run to the
peak at that rate (never under MIN_WEEKS), and a plan shorter than that is refused, not squeezed.
Weekly mileage grows by at most 10% a week (2 mi at low volume) over the last full week. Runners
without a base (MIN_LONG_RUN_MI, MIN_WEEKLY_MI) are told to build one first.

Pure functions over plain values; rows come back in the shape services/backup.parse_plan returns.
"""
import math
from typing import Any, Optional

from backend.app.core.errors import UnprocessableError
from backend.app.services.planning import DAYS

PEAK_LONG_RUN_MI = 20.0
BASE_LONG_RUN_MI = 13.0  # where a long plan holds the long run before the build
TAPER_LONG_RUNS_MI = (12.0, 8.0)  # the two weeks before race week
STEP_BACK_SHARE = 0.75  # a step-back week's runs, compared with the week before
MIN_WEEKS = 16
MIN_LONG_RUN_MI = 3.0
MIN_WEEKLY_MI = 10.0
WEEKLY_GROWTH = 0.10
WEEKLY_GROWTH_MIN_MI = 2.0  # 10% of a small week is less than the long run alone grows
MIN_RUN_MI = 2.0
RACE_WEEK_RUN_MI = 3.0
MAX_MIDWEEK_RUN_MI = 8.0
MAX_MIDWEEK_TOTAL_MI = 24.0
MIDWEEK_GROWTH_MI = 0.5  # each mid-week run, per full week
# Run days for each number of runs a week: the long run is Sunday (race day), the workout Thursday.
RUN_DAYS = {3: ("Tue", "Thu", "Sun"), 4: ("Tue", "Thu", "Sat", "Sun"), 5: ("Tue", "Wed", "Thu", "Sat", "Sun")}
TAPER_WEEKS = len(TAPER_LONG_RUNS_MI)

# The Thursday workout: (session, notes).
WORKOUTS = {
    "Easy": ("Easy Run", None),
    "Strides": ("Easy Run + Strides", "Easy pace, then 4-6 x 20 s strides with full recovery."),
    "Tempo": ("Tempo Run", "Warm up, 15-20 min comfortably hard, cool down."),
    "Hills": ("Hill Repeats", "Warm up, 6-8 x 60-90 s uphill at a strong effort, jog back down."),
    "Race-Pace Segments": ("Race-Pace Run", "Easy start, then 15-20 min at goal marathon pace."),
}


def _half(mi: float) -> float:
    return math.floor(mi * 2 + 1e-9) / 2


def _is_step_back(week: int, peak: int) -> bool:
    """Every fourth week before the peak, counting back from it (the week before the peak is one).
    The long run doesn't grow in these weeks; in the first two weeks, with nothing to recover from
    yet, it holds instead of dropping (_is_light)."""
    return week < peak and (peak - week) % 4 == 1


def _is_light(week: int, peak: int) -> bool:
    return _is_step_back(week, peak) and week > 2


def _ups_before(week: int, peak: int) -> int:
    """Weeks before `week` in which the long run grows (all but step-back weeks)."""
    return sum(1 for w in range(1, week) if not _is_step_back(w, peak))


def min_weeks(longest_run_mi: float) -> int:
    """The shortest safe plan (race week included) from this longest recent run."""
    peak = 1
    while min(longest_run_mi, PEAK_LONG_RUN_MI) + _ups_before(peak, peak) < PEAK_LONG_RUN_MI:
        peak += 1
    return max(MIN_WEEKS, peak + TAPER_WEEKS + 1)


def check_goal(weeks: int, longest_run_mi: float, weekly_mi: float, runs_per_week: int) -> None:
    if runs_per_week not in RUN_DAYS:
        raise UnprocessableError(f"Generated plans have {min(RUN_DAYS)} to {max(RUN_DAYS)} runs a week.")
    if longest_run_mi < MIN_LONG_RUN_MI or weekly_mi < MIN_WEEKLY_MI:
        raise UnprocessableError(
            f"A marathon plan needs a running base first: a run of at least {MIN_LONG_RUN_MI:g} mi and about "
            f"{MIN_WEEKLY_MI:g} mi a week. Build up to that, or start with an empty plan.")
    needed = min_weeks(longest_run_mi)
    if weeks < needed:
        raise UnprocessableError(
            f"From a longest run of {longest_run_mi:g} mi, building up safely takes at least {needed} weeks. "
            f"Pick a race at least {needed} weeks away, or start with an empty plan.")


def _long_runs(weeks: int, longest_run_mi: float) -> list[float]:
    """The Sunday long run of each week before race week."""
    peak = weeks - TAPER_WEEKS - 1
    start = min(longest_run_mi, PEAK_LONG_RUN_MI)
    hold = max(BASE_LONG_RUN_MI, start)
    runs: list[float] = []
    for w in range(1, peak + 1):
        if _is_step_back(w, peak):
            last = runs[-1] if runs else start
            runs.append(_half(STEP_BACK_SHARE * last) if _is_light(w, peak) else last)
        else:
            # Rising from where the runner is, but no higher than the build back from the peak.
            ups_after = sum(1 for x in range(w + 1, peak + 1) if not _is_step_back(x, peak))
            build = max(hold, PEAK_LONG_RUN_MI - ups_after)
            runs.append(_half(min(start + _ups_before(w, peak), build)))
    return runs + list(TAPER_LONG_RUNS_MI)


def _workout(week: int, peak: int, step_back: bool) -> str:
    if step_back or week > peak + 1:  # the last taper week is easy
        return "Easy"
    if week > peak:
        return "Strides"
    to_peak = peak - week
    if to_peak >= 12:
        return "Strides"
    if to_peak >= 5:
        return "Hills" if to_peak % 2 else "Tempo"
    return "Race-Pace Segments" if to_peak % 2 else "Tempo"


def _row(week: int, week_type: str, day: str, category: str, subtype: Optional[str], session: str,
         distance: Optional[float], notes: Optional[str]) -> dict[str, Any]:
    return {"plan_id": f"W{week}-{day}", "week": week, "day": day, "week_type": week_type, "category": category,
            "run_subtype": subtype, "planned_session": session, "target_distance_mi": distance,
            "target_duration_min": None, "notes": notes, "skipped": False, "skip_reason": None}


def generate_plan(weeks: int, longest_run_mi: float, weekly_mi: float, runs_per_week: int) -> list[dict[str, Any]]:
    """A full plan of `weeks` weeks ending with the marathon on the last Sunday. Raises
    UnprocessableError if the goal isn't safe (check_goal)."""
    check_goal(weeks, longest_run_mi, weekly_mi, runs_per_week)
    days = RUN_DAYS[runs_per_week]
    midweek_days = [d for d in days if d != "Sun"]
    peak = weeks - TAPER_WEEKS - 1
    long_runs = _long_runs(weeks, longest_run_mi)
    rows: list[dict[str, Any]] = []
    last_full_total = weekly_mi  # the last week that wasn't lighter on purpose
    # Mid-week runs start from what the runner does now besides their long run.
    last_mid = (weekly_mi - longest_run_mi) / len(midweek_days) - MIDWEEK_GROWTH_MI
    for week in range(1, weeks + 1):
        step_back = _is_light(week, peak)
        if week == weeks:
            week_type = "Race"
        elif week > peak:
            week_type = "Taper"
        elif week == peak:
            week_type = "Peak"
        else:
            week_type = "Step-back" if step_back else "Normal"

        if week == weeks:
            mid = RACE_WEEK_RUN_MI
        elif step_back or week > peak:
            mid = max(MIN_RUN_MI, _half(STEP_BACK_SHARE * last_mid))
        else:
            long_mi = long_runs[week - 1]
            cap = max(last_full_total * (1 + WEEKLY_GROWTH), last_full_total + WEEKLY_GROWTH_MIN_MI)
            n = len(midweek_days)
            mid = max(MIN_RUN_MI, _half(min(last_mid + MIDWEEK_GROWTH_MI, MAX_MIDWEEK_RUN_MI,
                                            MAX_MIDWEEK_TOTAL_MI / n, (cap - long_mi) / n)))
            last_full_total = long_mi + mid * n
            last_mid = mid

        workout = "Easy" if week == weeks else _workout(week, peak, step_back)
        for day in DAYS:
            if day not in days:
                rows.append(_row(week, week_type, day, "Rest", None, "Rest Day", None, None))
            elif day == "Sun" and week == weeks:
                rows.append(_row(week, week_type, day, "Race", None, "RACE DAY: Marathon", 26.2,
                                 "Race day. Start slower than you think, fuel early."))
            elif day == "Sun":
                note = ("Peak long run." if week == peak else "Step-back week, shorter on purpose." if step_back
                        else "Taper, trust the work already done." if week > peak else None)
                rows.append(_row(week, week_type, day, "Run", "Long Run", "Long Run", long_runs[week - 1], note))
            elif day == "Thu":
                session, notes = WORKOUTS[workout]
                if week == weeks:
                    session, notes = "Shakeout Run", "Easy shakeout, not a workout."
                rows.append(_row(week, week_type, day, "Run", workout, session, mid, notes))
            else:
                rows.append(_row(week, week_type, day, "Run", "Easy", "Easy Run", mid, None))
    return rows
