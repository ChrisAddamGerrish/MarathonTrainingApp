import { useData } from "../contexts/DataContext";
import { useDialogs } from "../contexts/DialogContext";
import { CATEGORIES, fmtDate, fmtMi, fmtMin, isRun, pace } from "../utils/format";
import { Chip, ClickableRow } from "../components/common";

const Dash = () => <span className="muted">—</span>;

export default function Log({ filters, setFilters }) {
  const { data } = useData();
  const { openNew, openEdit } = useDialogs();
  const { activities, weeks, plan } = data;

  const q = filters.q.trim().toLowerCase();
  const list = activities.filter(
    a =>
      (!filters.category || a.category === filters.category) &&
      (!filters.week || String(a.week) === filters.week) &&
      (!q || `${a.actual_session} ${a.notes || ""}`.toLowerCase().includes(q)),
  );
  const totals = list.reduce(
    (t, a) => ({ mi: t.mi + (isRun(a.category) ? a.distance_mi || 0 : 0), min: t.min + (a.duration_min || 0) }),
    { mi: 0, min: 0 },
  );
  const setFilter = key => e => setFilters(f => ({ ...f, [key]: e.target.value }));

  return (
    <div className="section" style={{ marginTop: 4 }}>
      <div className="section-head">
        <h2>Activity log</h2>
        <span className="sub num">
          {list.length} {list.length === 1 ? "activity" : "activities"} · {fmtMi(totals.mi)} run · {fmtMin(totals.min)}
        </span>
        <span className="spacer" />
        <button className="btn primary" onClick={openNew}>
          + Log activity
        </button>
      </div>
      <div className="filters" style={{ marginBottom: 12 }}>
        <select aria-label="Category" value={filters.category} onChange={setFilter("category")}>
          <option value="">All categories</option>
          {CATEGORIES.map(c => (
            <option key={c}>{c}</option>
          ))}
        </select>
        <select aria-label="Week" value={filters.week} onChange={setFilter("week")}>
          <option value="">All weeks</option>
          {weeks.map(w => (
            <option key={w.week} value={w.week}>
              Week {w.week}
            </option>
          ))}
        </select>
        <input
          type="search"
          placeholder="Search sessions & notes"
          aria-label="Search"
          value={filters.q}
          onChange={setFilter("q")}
        />
      </div>
      <div className="card">
        {list.length ? (
          <div className="table-scroll">
            <table className="data">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Category</th>
                  <th>Session</th>
                  <th className="r">Distance</th>
                  <th className="r">Time</th>
                  <th className="r">Pace</th>
                  <th className="r">kJ</th>
                  <th>Plan</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {list.map(a => {
                  const p = plan.find(p => p.plan_id === a.plan_id);
                  return (
                    <ClickableRow key={a.activity_id} title="Click to edit" onActivate={() => openEdit(a)}>
                      <td className="num" style={{ whiteSpace: "nowrap" }}>
                        {fmtDate(a.activity_date)}
                      </td>
                      <td>
                        <Chip category={a.category} />
                      </td>
                      <td>{a.actual_session}</td>
                      <td className="r num">{a.distance_mi ? fmtMi(a.distance_mi) : <Dash />}</td>
                      <td className="r num">{a.duration_min ? fmtMin(a.duration_min) : <Dash />}</td>
                      <td className="r num">
                        {isRun(a.category) && a.distance_mi ? pace(a.distance_mi, a.duration_min) : <Dash />}
                      </td>
                      <td className="r num">{a.output_kj ? +a.output_kj.toFixed(0) : <Dash />}</td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        {p ? (
                          <span className="muted" title={p.planned_session}>
                            {p.plan_id}
                          </span>
                        ) : (
                          <Dash />
                        )}
                      </td>
                      <td className="notes-cell">{a.notes || ""}</td>
                    </ClickableRow>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">No activities match these filters.</div>
        )}
      </div>
    </div>
  );
}
