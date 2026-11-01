import { useCallback, useState } from "react";
import { useData } from "../contexts/DataContext";
import { useToast } from "../contexts/ToastContext";
import { fmtDate, focusWeek } from "../utils/format";
import PlanWeek from "../components/PlanWeek";
import { weekHref } from "../utils/links";
import { planCsv } from "../utils/csv";
import BackupButtons from "../components/BackupButtons";
import RebuildDialog from "../components/RebuildDialog";

const SHORT_TYPE = { Normal: "", "Step-back": "Step-back", Taper: "Taper", Peak: "Peak", Race: "Race" };

export default function Plan({ arg }) {
  const { data, reload } = useData();
  const toast = useToast();
  const [rebuilding, setRebuilding] = useState(false);
  const { weeks, summary } = data;
  const selected = Math.min(Math.max(parseInt(arg, 10) || focusWeek(summary), 1), summary.last_week);
  const dateOpts = { month: "short", day: "numeric", year: "numeric" };
  const closeRebuild = useCallback(() => setRebuilding(false), []);
  const rebuilt = useCallback(
    async message => {
      setRebuilding(false);
      toast(message);
      await reload();
    },
    [toast, reload],
  );

  return (
    <>
      <div className="section" style={{ marginTop: 4 }}>
        <div className="section-head">
          <h2>Training plan</h2>
          <span className="sub">
            {summary.last_week} weeks · {fmtDate(summary.plan_start, dateOpts)} → {fmtDate(summary.race_date, dateOpts)}
          </span>
          <span className="spacer" />
          <button className="btn" title="Rebuild the weeks after this one for a new race date" onClick={() => setRebuilding(true)}>
            Rebuild plan
          </button>
          <BackupButtons kind="plan" exportCsv={() => planCsv(data.plan)} exportTitle="Download the whole plan" />
        </div>
        <nav className="weekpick" aria-label="Pick a week">
          {weeks.map(w => (
            <a
              key={w.week}
              href={weekHref(w.week)}
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
        <PlanWeek weekNo={selected} />
      </div>
      {rebuilding && <RebuildDialog onClose={closeRebuild} onDone={rebuilt} />}
    </>
  );
}
