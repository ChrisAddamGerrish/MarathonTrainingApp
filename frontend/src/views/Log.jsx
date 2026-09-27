import { useEffect } from "react";
import { useData } from "../contexts/DataContext";
import { useDialogs } from "../contexts/DialogContext";
import { CATEGORIES, fmtDate, fmtMi, fmtMin, isRun, pace } from "../utils/format";
import { Chip, ClickableRow } from "../components/common";
import MultiSelect from "../components/MultiSelect";
import StravaPanel from "../components/StravaPanel";
import { workoutsCsv } from "../utils/csv";
import BackupButtons from "../components/BackupButtons";
import { useToast } from "../contexts/ToastContext";

const Dash = () => <span className="muted">—</span>;

export default function Log({ arg, filters, setFilters }) {
  const { data } = useData();
  const { openNew, openEdit } = useDialogs();
  const toast = useToast();
  const { activities, weeks, plan } = data;

  // #/log/<id> (utils/links.js) opens that workout, then drops the id so closing the dialog sticks
  // and the same link works again. replace() keeps the back button going to where you came from.
  useEffect(() => {
    if (!arg) return;
    const a = activities.find(x => String(x.activity_id) === arg);
    if (a) openEdit(a);
    else toast(`Workout ${arg} wasn't found`, true);
    window.location.replace("#/log");
  }, [arg, activities, openEdit, toast]);

  const q = filters.q.trim().toLowerCase();
  const matching = activities.filter(
    a =>
      (!filters.categories.length || filters.categories.includes(a.category)) &&
      (!filters.weeks.length || filters.weeks.includes(String(a.week))) &&
      (!q || `${a.actual_session} ${a.notes || ""}`.toLowerCase().includes(q)),
  );
  // The server sends them newest first (by date, then by when they were logged).
  const list = filters.oldestFirst ? [...matching].reverse() : matching;
  const totals = list.reduce(
    (t, a) => ({ mi: t.mi + (isRun(a.category) ? a.distance_mi || 0 : 0), min: t.min + (a.duration_min || 0) }),
    { mi: 0, min: 0 },
  );
  const setFilter = key => e => setFilters(f => ({ ...f, [key]: e.target.value }));
  const setList = key => values => setFilters(f => ({ ...f, [key]: values }));
  const toggleDateOrder = () => setFilters(f => ({ ...f, oldestFirst: !f.oldestFirst }));

  return (
    <div className="section" style={{ marginTop: 4 }}>
      <div className="section-head">
        <h2>Activity log</h2>
        <span className="sub num">
          {list.length} {list.length === 1 ? "activity" : "activities"} · {fmtMi(totals.mi)} run · {fmtMin(totals.min)}
        </span>
        <span className="spacer" />
        <BackupButtons
          kind="workouts"
          exportCsv={list.length ? () => workoutsCsv(list, plan) : null}
          exportTitle={
            list.length < activities.length
              ? `Download the ${list.length} activities shown (clear the filters for a full backup)`
              : "Download every activity, each with a link to its details"
          }
        />
        <button className="btn primary" onClick={openNew}>
          + Log activity
        </button>
      </div>
      <StravaPanel />
      <div className="filters" style={{ marginBottom: 12 }}>
        <MultiSelect
          label="Categories"
          allLabel="All categories"
          noun="categories"
          options={CATEGORIES.map(c => ({ value: c, label: c, content: <Chip category={c} /> }))}
          selected={filters.categories}
          onChange={setList("categories")}
        />
        <MultiSelect
          label="Weeks"
          allLabel="All weeks"
          noun="weeks"
          options={weeks.map(w => ({ value: String(w.week), label: `Week ${w.week}` }))}
          selected={filters.weeks}
          onChange={setList("weeks")}
        />
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
                  <th aria-sort={filters.oldestFirst ? "ascending" : "descending"}>
                    <button
                      type="button"
                      className="th-sort"
                      onClick={toggleDateOrder}
                      title={filters.oldestFirst ? "Oldest first: click for newest first" : "Newest first: click for oldest first"}
                    >
                      Date <span aria-hidden="true">{filters.oldestFirst ? "▲" : "▼"}</span>
                    </button>
                  </th>
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
