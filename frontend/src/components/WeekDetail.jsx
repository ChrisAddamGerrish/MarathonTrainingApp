import { useState } from "react";
import { api, jsonRequest } from "../api";
import { useData } from "../DataContext";
import { useDialogs } from "../Dialogs";
import { DAYS, fmtDate, fmtMi, fmtMin, fmtSigned, isRun, pace } from "../lib/format";
import { useToast } from "../Toast";
import { Chip, ProgressBar, StatusPill } from "./common";

const joinParts = parts => parts.filter(Boolean).join(" · ");

// Sessions that can still be skipped: not done, not a rest day, not an optional slot.
const CAN_SKIP = new Set(["missed", "today", "upcoming"]);

/** Reason box inside the skip confirmation. It writes into `target.current` for the confirm handler. */
function ReasonField({ target }) {
  const [value, setValue] = useState("");
  return (
    <label className="field" style={{ marginTop: 12 }}>
      <span>Reason (optional)</span>
      <input
        type="text"
        maxLength={200}
        placeholder="e.g. sick, travel, extra rest"
        value={value}
        onChange={e => {
          setValue(e.target.value);
          target.current = e.target.value;
        }}
      />
    </label>
  );
}

function SessionRow({ r, linked }) {
  const { openLogPlan, openReview } = useDialogs();
  const { reload } = useData();
  const toast = useToast();

  function startSkip() {
    const reason = { current: "" };
    openReview({
      title: "Skip this session?",
      body: (
        <>
          <p className="hist-sub" style={{ marginBottom: 12 }}>
            It will show as Skipped instead of Missed, and won't count for or against your plan adherence. You can undo
            this any time.
          </p>
          <div className="diff">
            <div className="diff-row plain">
              <span className="k">Session</span>
              <span className="to">{r.planned_session}</span>
            </div>
            <div className="diff-row plain">
              <span className="k">Date</span>
              <span className="to">{fmtDate(r.date, { weekday: "short", month: "short", day: "numeric", year: "numeric" })}</span>
            </div>
          </div>
          <ReasonField target={reason} />
        </>
      ),
      confirmLabel: "Skip session",
      run: async () => {
        await api(`/api/plan/${encodeURIComponent(r.plan_id)}/skip`, jsonRequest("PUT", { reason: reason.current.trim() || null }));
        return "Session skipped";
      },
    });
  }

  async function undoSkip() {
    try {
      await api(`/api/plan/${encodeURIComponent(r.plan_id)}/skip`, { method: "DELETE" });
      toast("Skip removed");
      await reload();
    } catch (err) {
      toast(err.message, true);
    }
  }

  const isRest = r.category === "Rest";
  const hasTarget = r.target_distance_mi != null || r.target_duration_min != null;
  const targetTxt = joinParts([
    r.target_distance_mi != null ? fmtMi(r.target_distance_mi) : null,
    r.target_duration_min != null ? fmtMin(r.target_duration_min) : null,
  ]);
  const done = r.linked_activity_count > 0;
  const actualTxt =
    joinParts([
      r.actual_distance_mi > 0 ? fmtMi(r.actual_distance_mi) : null,
      r.actual_duration_min > 0 ? fmtMin(r.actual_duration_min) : null,
    ]) || "Logged";
  const variance =
    done && hasTarget
      ? r.target_distance_mi != null
        ? fmtSigned(r.distance_variance_mi, "mi")
        : fmtSigned(r.duration_variance_min, "min")
      : "";
  const meta = joinParts([
    r.run_subtype && r.run_subtype !== "Long Run" ? r.run_subtype : null,
    r.notes,
    r.status === "skipped" && r.skip_reason ? `Skipped: ${r.skip_reason}` : null,
  ]);

  return (
    <div className="sess" data-status={r.status}>
      <div>
        <div className="sess-name">
          <Chip category={r.category} />
          {" "}
          {r.planned_session}
        </div>
        {meta && <div className="sess-meta">{meta}</div>}
      </div>
      <div className="c-target">
        {!isRest && hasTarget && (
          <>
            <span className="lbl">Target</span>
            <span className="val num">{targetTxt}</span>
          </>
        )}
      </div>
      <div className="c-actual">
        {done && (
          <>
            <span className="lbl">Actual</span>
            <span className="val num">
              {actualTxt}
              {variance && <span className="var">{variance}</span>}
            </span>
          </>
        )}
      </div>
      <div className="c-status">
        <StatusPill status={r.status} />
      </div>
      <div className="c-act">
        {!isRest && !done && r.status !== "skipped" && (
          <button className="btn small" title="Log this session" onClick={() => openLogPlan(r.plan_id)}>
            Log
          </button>
        )}
        {CAN_SKIP.has(r.status) && (
          <button className="btn small ghost" title="Skip this session" onClick={startSkip}>
            Skip
          </button>
        )}
        {r.status === "skipped" && (
          <button className="btn small ghost" title="Remove the skip" onClick={undoSkip}>
            Undo skip
          </button>
        )}
      </div>
      {linked.length > 0 && (
        <div className="logged">
          {linked.map(a => (
            <div key={a.activity_id}>
              {a.actual_session}
              {a.distance_mi && isRun(a.category) ? <span className="muted"> · {pace(a.distance_mi, a.duration_min)}</span> : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ExtraRow({ a }) {
  const { openEdit } = useDialogs();
  return (
    <div className="sess extra" data-status="extra">
      <div>
        <div className="sess-name">
          <Chip category={a.category} />
          {" "}
          {a.actual_session}
          <span className="extra-tag">Unplanned</span>
        </div>
        {a.notes && <div className="sess-meta">{a.notes}</div>}
      </div>
      <div className="c-target" />
      <div className="c-actual">
        <span className="lbl">Actual</span>
        <span className="val num">
          {joinParts([a.distance_mi ? fmtMi(a.distance_mi) : null, a.duration_min ? fmtMin(a.duration_min) : null]) || "—"}
        </span>
      </div>
      <div className="c-status" />
      <div className="c-act">
        <button className="btn small ghost" onClick={() => openEdit(a)}>
          Edit
        </button>
      </div>
    </div>
  );
}

/** One training week: summary bars, then each day's planned sessions and unplanned extras. */
export default function WeekDetail({ weekNo }) {
  const { data } = useData();
  const { plan, activities, weeks, summary } = data;
  const wk = weeks.find(w => w.week === weekNo);
  const rows = plan.filter(p => p.week === weekNo);
  const acts = activities.filter(a => a.week === weekNo);

  return (
    <div className="card">
      <div className="week-head">
        <h3>Week {weekNo}</h3>
        <span className="type-tag">{wk.week_type}</span>
        <span className="muted">
          {fmtDate(wk.start, { month: "short", day: "numeric" })} – {fmtDate(wk.end, { month: "short", day: "numeric" })}
        </span>
        <span className="grow" />
      </div>
      <div className="wk-summary" style={{ borderBottom: "1px solid var(--line)" }}>
        <div>
          <div className="k">
            <span>Run miles</span>
            <b className="num">
              {fmtMi(wk.actual_run_mi)} <span className="muted">/ {fmtMi(wk.planned_run_mi)}</span>
            </b>
          </div>
          <ProgressBar actual={wk.actual_run_mi} planned={wk.planned_run_mi} />
        </div>
        <div>
          <div className="k">
            <span>Training time</span>
            <b className="num">
              {fmtMin(wk.actual_min)} <span className="muted">/ {fmtMin(wk.planned_min)}</span>
            </b>
          </div>
          <ProgressBar actual={wk.actual_min} planned={wk.planned_min} />
        </div>
        <div>
          <div className="k">
            <span>Sessions done</span>
            <b className="num">
              {wk.sessions_done} <span className="muted">/ {wk.sessions_planned}</span>
            </b>
          </div>
          <ProgressBar actual={wk.sessions_done} planned={wk.sessions_planned} />
        </div>
      </div>

      {DAYS.map(day => {
        const dayRows = rows.filter(r => r.day === day);
        const date = dayRows[0]?.date;
        const extras = acts.filter(a => a.activity_date === date && !a.plan_id);
        return (
          <section className={"day" + (date === summary.today ? " is-today" : "")} key={day}>
            <div className="day-label">
              <b>{day}</b>
              <span>{fmtDate(date, { month: "short", day: "numeric" })}</span>
            </div>
            <div className="sessions">
              {dayRows.map(r => (
                <SessionRow key={r.plan_id} r={r} linked={acts.filter(a => a.plan_id === r.plan_id)} />
              ))}
              {extras.map(a => (
                <ExtraRow key={a.activity_id} a={a} />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
