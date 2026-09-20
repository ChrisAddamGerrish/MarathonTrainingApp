import { useData } from "../contexts/DataContext";
import { fmtDate } from "./format";

export const FIELDS = [
  ["activity_date", "Date"],
  ["category", "Category"],
  ["actual_session", "Session"],
  ["distance_mi", "Distance"],
  ["duration_min", "Duration"],
  ["output_kj", "Output"],
  ["plan_id", "Plan link"],
  ["notes", "Notes"],
];
export const FIELD_LABEL = Object.fromEntries(FIELDS);

/** Treat "" and null/undefined as the same "empty" value when comparing. */
export const norm = v => (v === "" || v == null ? null : v);

export const diffActivity = (a, b) =>
  FIELDS.filter(([k]) => norm(a[k]) !== norm(b[k])).map(([k]) => ({ field: k, old: norm(a[k]), new: norm(b[k]) }));

function FieldValue({ field, value }) {
  const { data } = useData();
  const v = norm(value);
  if (v == null) return <span className="none">none</span>;
  switch (field) {
    case "activity_date":
      return fmtDate(v, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
    case "distance_mi":
      return `${+Number(v).toFixed(2)} mi`;
    case "duration_min":
      return `${+Number(v).toFixed(1)} min`;
    case "output_kj":
      return `${+Number(v).toFixed(1)} kJ`;
    case "plan_id": {
      const p = data.plan.find(p => p.plan_id === v);
      return p ? `${v} · ${p.planned_session}` : v;
    }
    default:
      return String(v);
  }
}

/** Old → new, one row per changed field. */
export function DiffRows({ changes }) {
  return (
    <div className="diff">
      {changes.map(c => (
        <div className="diff-row" key={c.field}>
          <span className="k">{FIELD_LABEL[c.field]}</span>
          <span className="from"><FieldValue field={c.field} value={c.old} /></span>
          <span className="arrow" aria-label="changed to">→</span>
          <span className="to"><FieldValue field={c.field} value={c.new} /></span>
        </div>
      ))}
    </div>
  );
}

/** All non-empty fields of one activity. */
export function DetailRows({ values }) {
  return (
    <div className="diff">
      {FIELDS.filter(([k]) => norm(values[k]) != null).map(([k, label]) => (
        <div className="diff-row plain" key={k}>
          <span className="k">{label}</span>
          <span className="to"><FieldValue field={k} value={values[k]} /></span>
        </div>
      ))}
    </div>
  );
}
