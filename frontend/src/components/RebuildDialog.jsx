import { useEffect, useRef, useState } from "react";
import { api, jsonRequest } from "../services/api";
import { useData } from "../contexts/DataContext";
import { fmtDate, fmtPace, parseDate } from "../utils/format";
import GoalFields, { goalBody } from "./GoalFields";

const plural = (n, noun) => `${n} ${noun}${n === 1 ? "" : "s"}`;
const LONG_DATE = { weekday: "short", month: "short", day: "numeric", year: "numeric" };

/**
 * Rebuild the weeks after this one from a goal (a new race date, or where the runner is now). The
 * form starts from the last four weeks of the log; the server says what would change before
 * anything is written. Calls onDone(message) after rebuilding, onClose() otherwise.
 */
export default function RebuildDialog({ onClose, onDone }) {
  const { data } = useData();
  const { summary } = data;
  const dialogRef = useRef(null);
  const [raceDate, setRaceDate] = useState(summary.race_date);
  const [goal, setGoal] = useState(null);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const el = dialogRef.current;
    const handleClose = () => {
      if (!el.open) onClose();
    };
    el.addEventListener("close", handleClose);
    el.showModal();
    return () => {
      el.removeEventListener("close", handleClose);
      if (el.open) el.close();
    };
  }, [onClose]);

  useEffect(() => {
    api("/api/plan/rebuild")
      .then(d =>
        setGoal({
          longest_run_mi: d.longest_run_mi ?? "",
          weekly_mi: d.weekly_mi ?? "",
          runs_per_week: String(d.runs_per_week),
          pace: fmtPace(d.easy_pace_min_per_mi),
          cross_training: [],
        }),
      )
      .catch(err => setError(err.message));
  }, []);

  const days = raceDate ? Math.round((parseDate(raceDate) - parseDate(summary.plan_start)) / 86400000) : NaN;
  const weeks = Math.floor(days / 7) + 1;
  const isSunday = days % 7 === 6;

  async function send(apply) {
    setBusy(true);
    setError("");
    try {
      const body = { weeks, goal: goalBody(goal) };
      const r = await api(`/api/plan/rebuild${apply ? "?apply=true" : ""}`, jsonRequest("POST", body));
      if (apply) {
        await onDone(`Plan rebuilt: race day ${fmtDate(r.race_date, LONG_DATE)}`);
      } else {
        setPreview(r);
        setBusy(false);
      }
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  function onSubmit(e) {
    e.preventDefault();
    if (!isSunday) return setError("Pick a Sunday for race day.");
    if (!e.target.checkValidity()) return setError("Check the highlighted fields.");
    send(false);
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="rebuild-title"
      onClick={e => {
        if (e.target === dialogRef.current) onClose();
      }}
    >
      {preview ? (
        <div id="review">
          <h2 id="rebuild-title">Rebuild the plan?</h2>
          <p className="hist-sub" style={{ marginBottom: 12 }}>
            {preview.kept_weeks
              ? `Weeks 1–${preview.kept_weeks} stay as they are. Weeks ${preview.kept_weeks + 1}–${preview.weeks} are replaced`
              : `All ${preview.weeks} weeks are replaced`}
            , with race day on <b>{fmtDate(preview.race_date, LONG_DATE)}</b>. Sessions you've added or changed in
            those weeks are replaced too. A copy of the database is saved to the backups folder first.
          </p>
          <ul className="restore-summary">
            {[
              preview.added && `${plural(preview.added, "planned session")} added`,
              preview.changed && `${plural(preview.changed, "planned session")} changed`,
              preview.removed && `${plural(preview.removed, "planned session")} removed`,
              preview.unlinked && `${plural(preview.unlinked, "logged workout")} unlinked from removed sessions`,
            ]
              .filter(Boolean)
              .map(l => (
                <li key={l}>{l}</li>
              ))}
          </ul>
          <div className="form-error" role="alert">
            {error}
          </div>
          <div className="actions">
            <span className="spacer" />
            <button type="button" className="btn" onClick={() => setPreview(null)}>
              Back
            </button>
            <button
              type="button"
              className={preview.unlinked ? "btn danger-solid" : "btn primary"}
              onClick={() => send(true)}
              disabled={busy}
              autoFocus
            >
              Rebuild
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={onSubmit} noValidate>
          <h2 id="rebuild-title">Rebuild plan</h2>
          <p className="hist-sub">
            Builds the weeks after this one from where you are now, ending with the marathon on race day. Filled in
            from your last four weeks of runs.
          </p>
          <label className="field">
            <span>Race day (a Sunday)</span>
            <input type="date" required value={raceDate} onChange={e => setRaceDate(e.target.value)} />
          </label>
          {goal ? (
            <GoalFields values={goal} onChange={(key, value) => setGoal(g => ({ ...g, [key]: value }))} />
          ) : (
            <p className="muted">Loading…</p>
          )}
          <p className="muted small-note">
            {raceDate && isSunday
              ? `Week ${weeks} of the plan. Check with a doctor first if you have any health concerns.`
              : raceDate
                ? `${fmtDate(raceDate)} isn't a Sunday.`
                : ""}
          </p>
          <div className="form-error" role="alert">
            {error}
          </div>
          <div className="actions">
            <span className="spacer" />
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn primary" disabled={busy || !goal}>
              Preview
            </button>
          </div>
        </form>
      )}
    </dialog>
  );
}
