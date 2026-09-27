import { useData } from "../contexts/DataContext";
import { FIELD_LABEL, FIELDS, norm } from "../utils/diff";
import { fmtDate } from "../utils/format";

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
