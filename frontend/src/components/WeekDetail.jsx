import { Fragment, useState } from "react";
import { api, jsonRequest } from "../services/api";
import { useData } from "../contexts/DataContext";
import { useDialogs } from "../contexts/DialogContext";
import { DAYS, fmtDate, fmtMi, fmtMin, fmtSigned, isRun, pace } from "../utils/format";
import { useToast } from "../contexts/ToastContext";
import { Chip, ProgressBar, StatusPill } from "./common";

const joinParts = parts => parts.filter(Boolean).join(" · ");

// Sessions that can still be skipped: not done, not a rest day, not an optional slot.
const CAN_SKIP = new Set(["missed", "today", "upcoming"]);

const STRAVA_NOTE = /^Imported from Strava: (https:\/\/www\.strava\.com\/activities\/\d+)$/;

/** How far a planned session's logged total is from its target ("" when there's no target). */
function varianceText(r) {
  if (r.target_distance_mi != null) return fmtSigned(r.distance_variance_mi, "mi");
  if (r.target_duration_min != null) return fmtSigned(r.duration_variance_min, "min");
  return "";
}

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

/** A planned session. `linked` is every workout counting toward it; `underneath` how many of them
 * were done on its own day and are listed below it (the rest show on the day they were done). */
function SessionRow({ r, linked, underneath }) {
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
  const targetTxt = joinParts([
    r.target_distance_mi != null ? fmtMi(r.target_distance_mi) : null,
    r.target_duration_min != null ? fmtMin(r.target_duration_min) : null,
  ]);
  const done = r.linked_activity_count > 0;
  // A single workout listed underneath shows its own numbers there; otherwise the total is here.
  const showTotal = linked.length > 1 || (linked.length === 1 && underneath === 0);
  const totalTxt =
    showTotal
      ? joinParts([
          r.actual_distance_mi > 0 ? fmtMi(r.actual_distance_mi) : null,
          r.actual_duration_min > 0 ? fmtMin(r.actual_duration_min) : null,
        ])
      : "";
  const variance = showTotal ? varianceText(r) : "";
  // Workouts done on another day stay on that day; say when here.
  const elsewhere = [...new Set(linked.filter(a => a.activity_date !== r.date).map(a => a.activity_date))];
  const meta = joinParts([
    elsewhere.length ? `Done ${elsewhere.map(d => fmtDate(d)).join(", ")}` : null,
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
        {!isRest && targetTxt && (
          <>
            <span className="lbl">Target</span>
            <span className="val num">{targetTxt}</span>
          </>
        )}
      </div>
      <div className="c-actual">
        {totalTxt && (
          <>
            <span className="lbl">Total</span>
            <span className="val num">
              {totalTxt}
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
    </div>
  );
}

/** A logged workout, laid out like a planned session and always on the day it was done: indented
 * under the planned session it counts toward when that is the same day, otherwise at day level,
 * tagged with the session it counts toward on another day (`countsToward`) or as Unplanned. */
function WorkoutRow({ a, indented, countsToward, variance }) {
  const { openEdit } = useDialogs();
  const strava = a.notes?.match(STRAVA_NOTE);
  const meta = [
    a.distance_mi && isRun(a.category) ? pace(a.distance_mi, a.duration_min) : null,
    a.notes && !strava ? a.notes : null,
  ].filter(Boolean);
  return (
    <div className={"sess workout " + (indented ? "linked" : "extra")} data-status="extra">
      <div className="c-name">
        <div className="sess-name">
          <Chip category={a.category} /> {a.actual_session}
          {countsToward ? (
            <span className="extra-tag counts">
              Counts toward {countsToward.week !== a.week ? `W${countsToward.week} ` : ""}
              {countsToward.day} · {countsToward.planned_session}
            </span>
          ) : (
            !indented && <span className="extra-tag">Unplanned</span>
          )}
        </div>
        {(meta.length > 0 || strava) && (
          <div className="sess-meta">
            {meta.join(" · ")}
            {strava && (
              <>
                {meta.length > 0 && " · "}
                <a href={strava[1]} target="_blank" rel="noreferrer">
                  Strava
                </a>
              </>
            )}
          </div>
        )}
      </div>
      <div className="c-target" />
      <div className="c-actual">
        <span className="lbl">Actual</span>
        <span className="val num">
          {joinParts([a.distance_mi ? fmtMi(a.distance_mi) : null, a.duration_min ? fmtMin(a.duration_min) : null]) || "—"}
          {variance && <span className="var">{variance}</span>}
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
  const planById = Object.fromEntries(plan.map(p => [p.plan_id, p]));
  // In the order they were done (the activity list comes newest first).
  const acts = activities
    .filter(a => a.week === weekNo)
    .sort((x, y) => x.activity_date.localeCompare(y.activity_date) || x.activity_id - y.activity_id);

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
        // From the week's start, not the day's sessions: an edited plan can leave a day empty.
        const d = new Date(wk.start + "T00:00:00");
        d.setDate(d.getDate() + DAYS.indexOf(day));
        const date = d.toLocaleDateString("en-CA"); // YYYY-MM-DD in local time
        // This day's workouts that aren't listed under one of this day's sessions: unplanned ones,
        // and ones counting toward a session planned for another day.
        const extras = acts.filter(
          a => a.activity_date === date && (!a.plan_id || planById[a.plan_id]?.date !== date),
        );
        return (
          <section className={"day" + (date === summary.today ? " is-today" : "")} key={day}>
            <div className="day-label">
              <b>{day}</b>
              <span>{fmtDate(date, { month: "short", day: "numeric" })}</span>
            </div>
            <div className="sessions">
              {dayRows.map(r => {
                // All of its workouts, from any day (or week); only same-day ones go underneath.
                const linked = activities.filter(a => a.plan_id === r.plan_id);
                const underneath = acts.filter(a => a.plan_id === r.plan_id && a.activity_date === r.date);
                return (
                  <Fragment key={r.plan_id}>
                    <SessionRow r={r} linked={linked} underneath={underneath.length} />
                    {underneath.map(a => (
                      <WorkoutRow
                        key={a.activity_id}
                        a={a}
                        indented
                        variance={linked.length === 1 ? varianceText(r) : ""}
                      />
                    ))}
                  </Fragment>
                );
              })}
              {extras.map(a => (
                <WorkoutRow key={a.activity_id} a={a} countsToward={a.plan_id ? planById[a.plan_id] : null} />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
