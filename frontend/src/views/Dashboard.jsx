import { useState } from "react";
import { useData } from "../contexts/DataContext";
import { useDialogs } from "../contexts/DialogContext";
import { activityStats, fmtDate, focusWeek } from "../utils/format";
import { weekHref, workoutHref } from "../utils/links";
import { Chip } from "../components/common";
import WeekDetail from "../components/WeekDetail";
import WeeklyChart from "../components/WeeklyChart";

export default function Dashboard() {
  const { data } = useData();
  const { openNew } = useDialogs();
  const [chartTable, setChartTable] = useState(false);
  const { summary: s, activities, weeks } = data;

  const fw = focusWeek(s);
  const before = s.current_week < 1;
  const after = s.current_week > s.last_week;
  const adherence = s.sessions_due ? Math.round((s.sessions_done / s.sessions_due) * 100) : null;
  const raceLabel = fmtDate(s.race_date, { weekday: "short", month: "short", day: "numeric", year: "numeric" });

  return (
    <>
      <div className="tiles">
        <div className="card tile">
          <div className="label">Race day</div>
          <div className="value num">
            {s.days_to_race > 0 ? s.days_to_race : s.days_to_race === 0 ? "Today" : "Done"}{" "}
            {s.days_to_race > 0 && <small>days</small>}
          </div>
          <div className="note">{raceLabel}</div>
        </div>
        <div className="card tile">
          <div className="label">Training week</div>
          <div className="value num">
            {before ? (
              "Starts soon"
            ) : after ? (
              "Complete"
            ) : (
              <>
                {s.current_week} <small>of {s.last_week}</small>
              </>
            )}
          </div>
          <div className="note">{weeks.find(w => w.week === fw).week_type} week</div>
        </div>
        <div className="card tile">
          <div className="label">Miles run</div>
          <div className="value num">
            {+s.total_run_mi.toFixed(1)} <small>mi</small>
          </div>
          <div className="note">{+s.planned_run_mi_to_date.toFixed(1)} mi planned so far</div>
        </div>
        <div className="card tile">
          <div className="label">Plan adherence</div>
          <div className="value num">
            {adherence == null ? (
              "—"
            ) : (
              <>
                {adherence}
                <small>%</small>
              </>
            )}
          </div>
          <div className="note">
            {s.sessions_done} of {s.sessions_due} due sessions done
            {s.sessions_skipped > 0 && ` · ${s.sessions_skipped} skipped`}
          </div>
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2>Weekly run miles</h2>
          <span className="sub">Planned vs. actual across the 20-week plan</span>
          <span className="spacer" />
          <button className="btn small" onClick={() => setChartTable(t => !t)}>
            {chartTable ? "Show chart" : "Show table"}
          </button>
        </div>
        <div className="card chart-card">
          <div className="legend">
            <span>
              <i style={{ background: "var(--series-planned)" }} />
              Planned
            </span>
            <span>
              <i style={{ background: "var(--series-actual)" }} />
              Actual
            </span>
          </div>
          <WeeklyChart weeks={weeks} summary={s} showTable={chartTable} />
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2>{before ? "First week" : after ? "Final week" : "This week"}</h2>
          <span className="spacer" />
          <a className="btn small" href={weekHref(fw)}>
            Open in Plan
          </a>
        </div>
        <WeekDetail weekNo={fw} />
      </div>

      <div className="section">
        <div className="section-head">
          <h2>Recent activity</h2>
          <span className="spacer" />
          <button className="btn small primary" onClick={openNew}>
            + Log activity
          </button>
          <a className="btn small" href="#/log">
            All {s.activity_count}
          </a>
        </div>
        <div className="card">
          {activities.length ? (
            <ul className="recent">
              {activities.slice(0, 6).map(a => (
                <li key={a.activity_id}>
                  <a href={workoutHref(a)} title="Open workout details">
                    <span className="when">{fmtDate(a.activity_date, { month: "short", day: "numeric" })}</span>
                    <span className="chipcell">
                      <Chip category={a.category} />
                    </span>
                    <span className="what">{a.actual_session}</span>
                    <span className="stats num">{activityStats(a)}</span>
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <div className="empty">No activity logged yet.</div>
          )}
        </div>
      </div>
    </>
  );
}
