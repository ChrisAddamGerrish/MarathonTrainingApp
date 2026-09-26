import { useEffect, useRef, useState } from "react";
import { api, jsonRequest } from "../services/api";
import { DAYS, PLAN_CATEGORIES } from "../utils/format";

const RUN_SUBTYPES = ["Easy", "Tempo", "Intervals", "Hills", "Strides", "Race-Pace Segments", "Long Run"];
const hasRunType = c => c === "Run" || c === "Race";
const toNumber = v => (v === "" || v == null ? null : Number(v));
const text = v => (v == null ? "" : String(v));

/**
 * Add or edit one planned session. `target` is { week, day } for a new session, or { week, row }
 * to edit an existing one. Calls onDone(message) after a save or delete, onClose() otherwise.
 */
export default function PlanSessionDialog({ target, onClose, onDone }) {
  const row = target.row;
  const dialogRef = useRef(null);
  const nameRef = useRef(null);
  const [values, setValues] = useState(() => ({
    day: row ? row.day : target.day,
    category: row ? row.category : "Run",
    run_subtype: text(row?.run_subtype),
    planned_session: text(row?.planned_session),
    target_distance_mi: text(row?.target_distance_mi),
    target_duration_min: text(row?.target_duration_min),
    notes: text(row?.notes),
  }));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    const el = dialogRef.current;
    const handleClose = () => {
      if (!el.open) onClose();
    };
    el.addEventListener("close", handleClose);
    el.showModal();
    nameRef.current?.focus();
    return () => {
      el.removeEventListener("close", handleClose);
      if (el.open) el.close();
    };
  }, [onClose]);

  const bind = name => ({ value: values[name], onChange: e => setValues(v => ({ ...v, [name]: e.target.value })) });
  const isRest = values.category === "Rest";

  async function run(action, message) {
    setBusy(true);
    setError("");
    try {
      await action();
      await onDone(message);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  function onSubmit(e) {
    e.preventDefault();
    if (!values.planned_session.trim()) {
      setError("Give the session a name.");
      return;
    }
    const body = {
      day: values.day,
      category: values.category,
      run_subtype: hasRunType(values.category) ? values.run_subtype || null : null,
      planned_session: values.planned_session.trim(),
      target_distance_mi: isRest ? null : toNumber(values.target_distance_mi),
      target_duration_min: isRest ? null : toNumber(values.target_duration_min),
      notes: values.notes.trim() || null,
    };
    run(
      () =>
        row
          ? api(`/api/plan/${encodeURIComponent(row.plan_id)}`, jsonRequest("PUT", body))
          : api(`/api/plan/weeks/${target.week}/sessions`, jsonRequest("POST", body)),
      row ? "Plan updated" : "Session added to the plan",
    );
  }

  const remove = () =>
    run(() => api(`/api/plan/${encodeURIComponent(row.plan_id)}`, { method: "DELETE" }), "Session removed from the plan");

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="plan-dlg-title"
      onClick={e => {
        if (e.target === dialogRef.current) onClose();
      }}
    >
      {confirmDelete ? (
        <div id="review">
          <h2 id="plan-dlg-title">Remove this session from the plan?</h2>
          <p className="hist-sub">
            <b>{row.planned_session}</b> on Week {target.week} {row.day}. Activities you've logged aren't affected.
          </p>
          <div className="form-error" role="alert">
            {error}
          </div>
          <div className="actions">
            <span className="spacer" />
            <button type="button" className="btn" onClick={() => setConfirmDelete(false)}>
              Back
            </button>
            <button type="button" className="btn danger-solid" onClick={remove} disabled={busy} autoFocus>
              Remove session
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={onSubmit} noValidate>
          <h2 id="plan-dlg-title">
            {row ? "Edit planned session" : "Add a session"} · Week {target.week}
          </h2>
          <div className="grid2">
            <label className="field">
              <span>Day</span>
              <select {...bind("day")}>
                {DAYS.map(d => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Category</span>
              <select {...bind("category")}>
                {PLAN_CATEGORIES.map(c => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
          </div>
          <label className="field">
            <span>Session</span>
            <input
              type="text"
              required
              maxLength={200}
              placeholder="e.g. Tempo 40 · Matt Wilpers"
              ref={nameRef}
              {...bind("planned_session")}
            />
          </label>
          {hasRunType(values.category) && (
            <label className="field">
              <span>Run type</span>
              <select {...bind("run_subtype")}>
                <option value="">—</option>
                {RUN_SUBTYPES.map(t => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </label>
          )}
          {!isRest && (
            <div className="grid2">
              <label className="field">
                <span>Target distance (mi)</span>
                <input type="number" step="0.01" min="0" inputMode="decimal" {...bind("target_distance_mi")} />
              </label>
              <label className="field">
                <span>Target duration (min)</span>
                <input type="number" step="1" min="0" inputMode="decimal" {...bind("target_duration_min")} />
              </label>
            </div>
          )}
          <label className="field">
            <span>Notes</span>
            <textarea rows={2} {...bind("notes")} />
          </label>
          <div className="form-error" role="alert">
            {error}
          </div>
          <div className="actions">
            {row && (
              <button type="button" className="btn danger" onClick={() => setConfirmDelete(true)}>
                Remove
              </button>
            )}
            <span className="spacer" />
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn primary" disabled={busy}>
              {row ? "Save" : "Add"}
            </button>
          </div>
        </form>
      )}
    </dialog>
  );
}
