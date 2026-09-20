import { useData } from "../DataContext";
import { fmtDate, focusWeek } from "../lib/format";
import WeekDetail from "../components/WeekDetail";

const SHORT_TYPE = { Normal: "", "Step-back": "Step-back", Taper: "Taper", Peak: "Peak", Race: "Race" };

export default function Plan({ arg }) {
  const { data } = useData();
  const { weeks, summary } = data;
  const selected = Math.min(Math.max(parseInt(arg, 10) || focusWeek(summary), 1), summary.last_week);
  const dateOpts = { month: "short", day: "numeric", year: "numeric" };

  return (
    <>
      <div className="section" style={{ marginTop: 4 }}>
        <div className="section-head">
          <h2>Training plan</h2>
          <span className="sub">
            {summary.last_week} weeks · {fmtDate(summary.plan_start, dateOpts)} → {fmtDate(summary.race_date, dateOpts)}
          </span>
        </div>
        <nav className="weekpick" aria-label="Pick a week">
          {weeks.map(w => (
            <a
              key={w.week}
              href={`#/plan/${w.week}`}
              aria-current={w.week === selected ? "true" : undefined}
              className={w.week === summary.current_week ? "now" : ""}
              title={`Week ${w.week} · ${w.week_type}`}
            >
              {w.week}
              <small>{SHORT_TYPE[w.week_type] || " "}</small>
            </a>
          ))}
        </nav>
      </div>
      <div className="section">
        <WeekDetail weekNo={selected} />
      </div>
    </>
  );
}
