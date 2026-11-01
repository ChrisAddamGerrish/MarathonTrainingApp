import { parsePace } from "../utils/format";

const CROSS_TRAINING = ["Strength", "Bike"];

/**
 * Where the runner is now, for generating a plan (registration and Rebuild plan). `values` holds
 * the inputs as typed: longest_run_mi, weekly_mi, runs_per_week, pace ("10:30") and
 * cross_training (a list); onChange(key, value) updates one.
 */
export default function GoalFields({ values, onChange }) {
  const set = key => e => onChange(key, e.target.value);
  const toggle = kind => e =>
    onChange(
      "cross_training",
      e.target.checked ? [...values.cross_training, kind] : values.cross_training.filter(k => k !== kind),
    );
  return (
    <>
      <div className="grid2">
        <label className="field">
          <span>Longest run, last 4 weeks (mi)</span>
          <input
            type="number"
            inputMode="decimal"
            min={0}
            max={50}
            step="any"
            required
            value={values.longest_run_mi}
            onChange={set("longest_run_mi")}
          />
        </label>
        <label className="field">
          <span>Miles a week now</span>
          <input
            type="number"
            inputMode="decimal"
            min={0}
            max={200}
            step="any"
            required
            value={values.weekly_mi}
            onChange={set("weekly_mi")}
          />
        </label>
      </div>
      <div className="grid2">
        <label className="field">
          <span>Runs a week</span>
          <select value={values.runs_per_week} onChange={set("runs_per_week")}>
            {[3, 4, 5].map(n => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Easy pace (min/mi)</span>
          <input
            inputMode="decimal"
            placeholder="10:30"
            pattern="\d{1,2}(:[0-5]\d)?|\d{1,2}\.\d+"
            title="Minutes a mile, like 10:30"
            value={values.pace}
            onChange={set("pace")}
          />
        </label>
      </div>
      <div className="field">
        <span>Other training on non-run days</span>
        <div className="check-row">
          {CROSS_TRAINING.map(kind => (
            <label className="check" key={kind}>
              <input type="checkbox" checked={values.cross_training.includes(kind)} onChange={toggle(kind)} />
              {kind}
            </label>
          ))}
        </div>
      </div>
    </>
  );
}

/** The goal the API takes, from GoalFields' values. */
export const goalBody = values => ({
  longest_run_mi: parseFloat(values.longest_run_mi),
  weekly_mi: parseFloat(values.weekly_mi),
  runs_per_week: parseInt(values.runs_per_week, 10),
  easy_pace_min_per_mi: parsePace(values.pace),
  cross_training: values.cross_training,
});
