import { useEffect, useMemo, useRef, useState } from "react";
import { api, jsonRequest } from "../api";
import { useData } from "../DataContext";
import { DetailRows, DiffRows, diffActivity } from "../lib/diff";
import { CATEGORIES } from "../lib/format";
import { planLinkWarnings } from "../lib/planLink";

const str = v => (v == null ? "" : String(v));
const toNumber = s => (s === "" || s == null ? null : Number(s));

function initialValues(activity, prefill) {
  const v = activity ?? {
    activity_date: "",
    category: "Run",
    actual_session: "",
    distance_mi: null,
    duration_min: null,
    output_kj: null,
    plan_id: "",
    notes: "",
    ...prefill,
  };
  return {
    activity_date: v.activity_date,
    category: v.category,
    actual_session: v.actual_session,
    distance_mi: str(v.distance_mi),
    duration_min: str(v.duration_min),
    output_kj: str(v.output_kj),
    plan_id: v.plan_id || "",
    notes: str(v.notes),
  };
}

/** <option>s for the "counts toward planned session" select: this date's sessions first. */
function PlanOptions({ plan, dateIso, selected }) {
  const rows = plan.filter(p => p.category !== "Rest" || p.plan_id === selected);
  const label = p => `W${p.week} ${p.day} · ${p.planned_session}`;
  const same = rows.filter(p => p.date === dateIso);
  const byWeek = {};
  rows.filter(p => p.date !== dateIso).forEach(p => (byWeek[p.week] ||= []).push(p));
  const opt = p => (
    <option key={p.plan_id} value={p.plan_id}>
      {label(p)}
    </option>
  );
  return (
    <>
      <option value="">— Not linked (unplanned) —</option>
      {same.length > 0 && <optgroup label="Planned for this date">{same.map(opt)}</optgroup>}
      {Object.entries(byWeek).map(([w, ps]) => (
        <optgroup key={w} label={`Week ${w}`}>
          {ps.map(opt)}
        </optgroup>
      ))}
    </>
  );
}

/**
 * The modal. `session.kind === "activity"` shows the form (new or edit); edits and deletes go
 * through a review step before anything is written. `kind === "review"` opens straight into a
 * review screen with no form behind it.
 */
export default function ActivityDialog({ session, onClose, onFinish }) {
  const { data } = useData();
  const original = session.kind === "activity" ? session.activity : null;
  const isEdit = original != null;
  const reviewOnly = session.kind === "review";

  const dialogRef = useRef(null);
  const sessionInputRef = useRef(null);
  const [values, setValues] = useState(() =>
    reviewOnly
      ? null
      : initialValues(original, { activity_date: data.summary.today, ...session.prefill }),
  );
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);
  const [review, setReview] = useState(reviewOnly ? session.review : null);
  const [reviewError, setReviewError] = useState("");
  const [confirming, setConfirming] = useState(false);

  // Open as a native modal; closing it any way (Esc, backdrop, buttons) reports back via onClose.
  useEffect(() => {
    const el = dialogRef.current;
    // A `close` event can arrive after StrictMode's remount has already re-opened the dialog.
    const handleClose = () => {
      if (!el.open) onClose();
    };
    el.addEventListener("close", handleClose);
    el.showModal();
    sessionInputRef.current?.focus();
    return () => {
      el.removeEventListener("close", handleClose);
      if (el.open) el.close();
    };
  }, [onClose]);

  const warnings = useMemo(
    () => (values ? planLinkWarnings(data.plan, original, values.plan_id, values.activity_date) : []),
    [data.plan, original, values],
  );

  const set = (name, value) => setValues(v => ({ ...v, [name]: value }));
  const bind = name => ({ value: values[name], onChange: e => set(name, e.target.value) });

  function onPlanChange(e) {
    const planId = e.target.value;
    const p = data.plan.find(x => x.plan_id === planId);
    setValues(v => ({
      ...v,
      plan_id: planId,
      // Picking a planned session fills in whatever is still blank from it.
      actual_session: p && !v.actual_session.trim() ? p.planned_session : v.actual_session,
      category: p && p.category !== "Rest" && !isEdit ? p.category : v.category,
    }));
  }

  async function onSubmit(e) {
    e.preventDefault();
    if (!values.activity_date || !values.actual_session.trim()) {
      setFormError("Date and session are required.");
      return;
    }
    const body = {
      activity_date: values.activity_date,
      category: values.category,
      actual_session: values.actual_session.trim(),
      distance_mi: toNumber(values.distance_mi),
      duration_min: toNumber(values.duration_min),
      output_kj: toNumber(values.output_kj),
      plan_id: values.plan_id || null,
      notes: values.notes.trim() || null,
    };
    setFormError("");

    if (!isEdit) {
      // New activities save straight away; History records them.
      setSaving(true);
      try {
        await api("/api/activities", jsonRequest("POST", body));
        await onFinish("Activity logged");
      } catch (err) {
        setFormError(err.message);
        setSaving(false);
      }
      return;
    }

    // Edits go through a review step first.
    const changes = diffActivity(original, body);
    if (!changes.length) {
      onFinish("No changes to save", { refresh: false });
      return;
    }
    const linkChanged = changes.some(c => c.field === "plan_id" || c.field === "activity_date");
    const warns = linkChanged ? planLinkWarnings(data.plan, original, body.plan_id, body.activity_date) : [];
    setReview({
      title: "Review changes",
      body: (
        <>
          <DiffRows changes={changes} />
          {warns.length > 0 && (
            <div className="warn-box" style={{ marginTop: 12 }}>
              <b>Check the plan link:</b> {warns.join(" ")}
            </div>
          )}
        </>
      ),
      confirmLabel: "Confirm & save",
      run: async () => {
        await api(`/api/activities/${original.activity_id}`, jsonRequest("PUT", body));
        return "Activity updated";
      },
    });
  }

  function askDelete() {
    setReview({
      title: "Delete this activity?",
      body: (
        <>
          <p className="hist-sub" style={{ marginBottom: 12 }}>
            It will be removed from marathon.db. The deletion is recorded in History, but the activity isn't restored
            automatically.
          </p>
          <DetailRows values={original} />
        </>
      ),
      confirmLabel: "Delete activity",
      danger: true,
      run: async () => {
        await api(`/api/activities/${original.activity_id}`, { method: "DELETE" });
        return "Activity deleted";
      },
    });
  }

  async function confirm() {
    setConfirming(true);
    setReviewError("");
    try {
      const message = await review.run();
      await onFinish(message);
    } catch (err) {
      setReviewError(err.message);
      setConfirming(false);
    }
  }

  const goBack = () => (reviewOnly ? onClose() : setReview(null));

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={review ? "review-title" : "dlg-title"}
      onClick={e => {
        if (e.target === dialogRef.current) onClose(); // click on the backdrop
      }}
    >
      {values && (
        <form hidden={!!review} onSubmit={onSubmit} noValidate>
          <h2 id="dlg-title">{isEdit ? "Edit activity" : "Log activity"}</h2>
          <div className="grid2">
            <label className="field">
              <span>Date</span>
              <input type="date" required {...bind("activity_date")} />
            </label>
            <label className="field">
              <span>Category</span>
              <select required {...bind("category")}>
                {CATEGORIES.map(c => (
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
              placeholder="e.g. 45 min Power Zone Ride — Matt Wilpers"
              ref={sessionInputRef}
              {...bind("actual_session")}
            />
          </label>
          <div className="grid3">
            <label className="field">
              <span>Distance (mi)</span>
              <input type="number" step="0.01" min="0" inputMode="decimal" {...bind("distance_mi")} />
            </label>
            <label className="field">
              <span>Duration (min)</span>
              <input type="number" step="0.1" min="0" inputMode="decimal" {...bind("duration_min")} />
            </label>
            <label className="field">
              <span>Output (kJ)</span>
              <input type="number" step="0.1" min="0" inputMode="decimal" {...bind("output_kj")} />
            </label>
          </div>
          <div className="field">
            <label htmlFor="plan-select">
              <span>Counts toward planned session</span>
            </label>
            <select id="plan-select" value={values.plan_id} onChange={onPlanChange}>
              <PlanOptions plan={data.plan} dateIso={values.activity_date} selected={values.plan_id} />
            </select>
            <div className={"hint" + (warnings.length ? " warn" : "")} aria-live="polite">
              {warnings.join(" ")}
            </div>
          </div>
          <label className="field">
            <span>Notes</span>
            <textarea rows={2} {...bind("notes")} />
          </label>
          <div className="form-error" role="alert">
            {formError}
          </div>
          <div className="actions">
            {isEdit && (
              <button type="button" className="btn danger" onClick={askDelete}>
                Delete
              </button>
            )}
            <span className="spacer" />
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn primary" disabled={saving}>
              Save
            </button>
          </div>
        </form>
      )}
      {review && (
        <div id="review">
          <h2 id="review-title">{review.title}</h2>
          <div>{review.body}</div>
          <div className="form-error" role="alert">
            {reviewError}
          </div>
          <div className="actions">
            <span className="spacer" />
            <button type="button" className="btn" onClick={goBack}>
              {reviewOnly ? "Cancel" : "Back"}
            </button>
            <button
              type="button"
              className={"btn " + (review.danger ? "danger-solid" : "primary")}
              onClick={confirm}
              disabled={confirming}
              autoFocus
            >
              {review.confirmLabel}
            </button>
          </div>
        </div>
      )}
    </dialog>
  );
}
