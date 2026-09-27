import { isRun, pace } from "./format";
import { workoutHref } from "./links";

/** One CSV field. Text a spreadsheet would run as a formula (=, +, -, @) gets a leading quote. */
function cell(value) {
  if (value == null) return "";
  let s = String(value);
  if (typeof value === "string" && /^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** CSV text (RFC 4180, CRLF) from rows and [header, row => value] columns. */
export function toCsv(rows, columns) {
  const lines = [columns.map(([h]) => cell(h)), ...rows.map(r => columns.map(([, get]) => cell(get(r))))];
  return lines.map(l => l.join(",")).join("\r\n") + "\r\n";
}

const BOM = String.fromCharCode(0xfeff);

/** Saves CSV text as a file. The BOM makes Excel read it as UTF-8. */
export function downloadCsv(filename, text) {
  const url = URL.createObjectURL(new Blob([BOM, text], { type: "text/csv;charset=utf-8" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* The two exports below double as backups: backend/app/services/backup.py reads them back by
 * header, so keep their headers in step with it. Columns it doesn't know are ignored there. */

/** Logged workouts as CSV, each with a link that opens its details in the app. */
export function workoutsCsv(activities, plan) {
  const planById = Object.fromEntries(plan.map(p => [p.plan_id, p]));
  return toCsv(activities, [
    ["Activity ID", a => a.activity_id],
    ["Date", a => a.activity_date],
    ["Week", a => a.week],
    ["Category", a => a.category],
    ["Session", a => a.actual_session],
    ["Distance (mi)", a => a.distance_mi],
    ["Duration (min)", a => a.duration_min],
    ["Pace (/mi)", a => (isRun(a.category) && a.distance_mi ? pace(a.distance_mi, a.duration_min).replace(" /mi", "") : null)],
    ["Output (kJ)", a => a.output_kj],
    ["Plan ID", a => a.plan_id],
    ["Planned session", a => planById[a.plan_id]?.planned_session],
    ["Notes", a => a.notes],
    ["Details", a => new URL(workoutHref(a), window.location.href).href],
  ]);
}

/** The training plan as CSV, in plan order (which sets the order of sessions within a day). */
export function planCsv(plan) {
  return toCsv(plan, [
    ["Plan ID", p => p.plan_id],
    ["Week", p => p.week],
    ["Day", p => p.day],
    ["Date", p => p.date],
    ["Week type", p => p.week_type],
    ["Category", p => p.category],
    ["Run type", p => p.run_subtype],
    ["Session", p => p.planned_session],
    ["Target distance (mi)", p => p.target_distance_mi],
    ["Target duration (min)", p => p.target_duration_min],
    ["Notes", p => p.notes],
    ["Skipped", p => (p.skipped ? "yes" : "")],
    ["Skip reason", p => p.skip_reason],
  ]);
}
