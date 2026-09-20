import { fmtDate } from "./format";

/** Warnings about linking an activity to a planned session (wrong date, already filled). */
export function planLinkWarnings(plan, original, planId, dateIso) {
  const p = planId && plan.find(x => x.plan_id === planId);
  if (!p) return [];
  const out = [];
  if (p.date !== dateIso) {
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
