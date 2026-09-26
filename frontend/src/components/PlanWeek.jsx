import { useCallback, useState } from "react";
import { api, jsonRequest } from "../services/api";
import { useData } from "../contexts/DataContext";
import { useToast } from "../contexts/ToastContext";
import { DAYS, fmtDate, fmtMi, fmtMin, isRun } from "../utils/format";
import { Chip } from "./common";
import PlanSessionDialog from "./PlanSessionDialog";

const WEEK_TYPES = ["Normal", "Step-back", "Peak", "Taper", "Race"];
const joinParts = parts => parts.filter(Boolean).join(" · ");

function PlanRow({ r, onEdit }) {
  const meta = joinParts([r.run_subtype && r.run_subtype !== "Long Run" ? r.run_subtype : null, r.notes]);
  const target = joinParts([
    r.target_distance_mi != null ? fmtMi(r.target_distance_mi) : null,
    r.target_duration_min != null ? fmtMin(r.target_duration_min) : null,
  ]);
  const quiet = r.category === "Rest" || r.optional;
  return (
    <div className={"plan-sess" + (quiet ? " quiet" : "")}>
      <div>
        <div className="sess-name">
          <Chip category={r.category} /> {r.planned_session}
        </div>
        {meta && <div className="sess-meta">{meta}</div>}
      </div>
      <div className="plan-target num">{target}</div>
      <button className="btn small ghost" onClick={() => onEdit(r)} aria-label={`Edit ${r.planned_session}`}>
        Edit
      </button>
    </div>
  );
}

/** One week of the training plan, as planned: sessions and targets, editable. No logged workouts. */
export default function PlanWeek({ weekNo }) {
  const { data, reload } = useData();
  const toast = useToast();
  const { plan, weeks, summary } = data;
  const [editing, setEditing] = useState(null);
  const wk = weeks.find(w => w.week === weekNo);
  const rows = plan.filter(p => p.week === weekNo);
  const sessions = rows.filter(r => r.category !== "Rest" && !r.optional).length;
  const runMiles = rows.filter(r => isRun(r.category)).reduce((t, r) => t + (r.target_distance_mi || 0), 0);

  const close = useCallback(() => setEditing(null), []);
  const done = useCallback(
    async message => {
      setEditing(null);
      toast(message);
      await reload();
    },
    [toast, reload],
  );

  async function changeWeekType(e) {
    try {
      await api(`/api/plan/weeks/${weekNo}`, jsonRequest("PUT", { week_type: e.target.value }));
      toast(`Week ${weekNo} is now a ${e.target.value} week`);
      await reload();
    } catch (err) {
      toast(err.message, true);
    }
  }

  return (
    <div className="card">
      <div className="week-head">
        <h3>Week {weekNo}</h3>
        <select className="type-select" aria-label="Week type" value={wk.week_type} onChange={changeWeekType}>
          {WEEK_TYPES.map(t => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <span className="muted">
          {fmtDate(wk.start, { month: "short", day: "numeric" })} – {fmtDate(wk.end, { month: "short", day: "numeric" })}
        </span>
        <span className="grow" />
      </div>
      <div className="plan-totals muted num">
        {sessions} sessions · {fmtMi(runMiles)} run · {fmtMin(wk.planned_min)} planned
      </div>

      {DAYS.map(day => {
        const dayRows = rows.filter(r => r.day === day);
        const date = new Date(wk.start + "T00:00:00");
        date.setDate(date.getDate() + DAYS.indexOf(day));
        const iso = date.toLocaleDateString("en-CA"); // YYYY-MM-DD in local time
        return (
          <section className={"day" + (iso === summary.today ? " is-today" : "")} key={day}>
            <div className="day-label">
              <b>{day}</b>
              <span>{fmtDate(iso, { month: "short", day: "numeric" })}</span>
            </div>
            <div className="sessions">
              {dayRows.map(r => (
                <PlanRow key={r.plan_id} r={r} onEdit={row => setEditing({ week: weekNo, row })} />
              ))}
              <button className="btn small ghost add-sess" onClick={() => setEditing({ week: weekNo, day })}>
                + Add session
              </button>
            </div>
          </section>
        );
      })}

      {editing && <PlanSessionDialog target={editing} onClose={close} onDone={done} />}
    </div>
  );
}
