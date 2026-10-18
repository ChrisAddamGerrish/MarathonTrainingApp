import { fmtDate } from "./format";

/** 1-based plan week of an ISO date; plan_start is the Monday of week 1 (as planning.week_of). */
export function planWeekOf(dateIso, planStart) {
  if (!dateIso || !planStart) return null;
  const days = (Date.parse(dateIso + "T00:00:00Z") - Date.parse(planStart + "T00:00:00Z")) / 86400000;
  return Math.floor(days / 7) + 1;
}

/** Warnings about linking an activity to a planned session (other week, wrong date, already filled). */
export function planLinkWarnings(plan, planStart, original, planId, dateIso) {
  const p = planId && plan.find(x => x.plan_id === planId);
  if (!p) return [];
  const out = [];
  const week = planWeekOf(dateIso, planStart);
  if (week != null && p.week !== week) {
    out.push(`${p.plan_id} is in week ${p.week}, but this activity is in week ${week}; it can only count toward a session of its own week.`);
  } else if (p.date !== dateIso) {
    out.push(`${p.plan_id} is planned for ${fmtDate(p.date)}, but this activity is dated ${fmtDate(dateIso)}.`);
  }
  const others = p.linked_activity_count - (original?.plan_id === planId ? 1 : 0);
  if (others > 0) {
    out.push(
      `${p.plan_id} already has ${others} other logged ${others === 1 ? "activity" : "activities"}; this one would count toward its totals too.`,
    );
  }
  return out;
}
