import { useEffect, useState } from "react";
import { api } from "../api";
import { useData } from "../DataContext";
import { useDialogs } from "../Dialogs";
import { DetailRows, DiffRows } from "../lib/diff";
import { activityStats, fmtDate } from "../lib/format";
import { revertInfo } from "../lib/revert";
import { useToast } from "../Toast";

const ACTION_LABEL = { INSERT: "Added", UPDATE: "Edited", DELETE: "Deleted" };

function fmtStamp(iso) {
  const d = new Date(iso);
  const opts = { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
  if (d.getFullYear() !== new Date().getFullYear()) opts.year = "numeric";
  return d.toLocaleString(undefined, opts);
}

const summarize = r =>
  [fmtDate(r.activity_date), r.category, activityStats(r), r.plan_id ? `linked to ${r.plan_id}` : null]
    .filter(Boolean)
    .join(" · ");

const Note = ({ children }) => (
  <p className="hist-sub" style={{ marginBottom: 12 }}>
    {children}
  </p>
);

/** What to show in the confirmation for reverting one entry. Built lazily, per kind of entry. */
function revertReview(entry, rv) {
  const name = (entry.new || entry.old).actual_session;
  switch (entry.action) {
    case "UPDATE":
      return {
        title: "Revert this edit?",
        confirmLabel: "Revert changes",
        done: "Change reverted",
        body: (
          <>
            <Note>
              Restores these fields on <b>{name}</b>. Its other fields stay as they are.
            </Note>
            <DiffRows changes={rv.changes} />
            {rv.stale > 0 && (
              <div className="warn-box" style={{ marginTop: 12 }}>
                <b>Edited since:</b> {rv.stale === 1 ? "one of these fields was" : `${rv.stale} of these fields were`}{" "}
                changed again after this edit. Reverting overwrites the newer value.
              </div>
            )}
          </>
        ),
      };
    case "INSERT":
      return {
        title: "Undo this add?",
        confirmLabel: "Delete activity",
        danger: true,
        done: "Activity removed",
        body: (
          <>
            <Note>This removes the activity that was added.</Note>
            <DetailRows values={rv.cur} />
          </>
        ),
      };
    default:
      return {
        title: "Restore this activity?",
        confirmLabel: "Restore activity",
        done: "Activity restored",
        body: (
          <>
            <Note>This re-creates the deleted activity, with its original ID and values.</Note>
            <DetailRows values={entry.old} />
          </>
        ),
      };
  }
}

export default function History() {
  const { data } = useData();
  const { openReview } = useDialogs();
  const toast = useToast();
  const [history, setHistory] = useState(null);
  const [error, setError] = useState(null);

  // Refetch whenever the activity data was reloaded (i.e. after any change).
  useEffect(() => {
    let cancelled = false;
    setHistory(null);
    api("/api/history")
      .then(h => !cancelled && (setHistory(h), setError(null)))
      .catch(e => !cancelled && (setHistory([]), setError(e.message)));
    return () => {
      cancelled = true;
    };
  }, [data]);

  if (history == null) return <div className="loading">Loading history…</div>;

  const revertedIds = new Set(history.filter(x => x.reverts).map(x => x.reverts));
  const stampOf = id => {
    const t = history.find(x => x.history_id === id);
    return t ? fmtStamp(t.changed_at) : "an earlier change";
  };

  function startRevert(entry) {
    const rv = revertInfo(entry, data.activities, revertedIds);
    if (!rv.ok) {
      toast(rv.note, true);
      return;
    }
    const { done, ...review } = revertReview(entry, rv);
    openReview({
      ...review,
      run: async () => {
        await api(`/api/history/${entry.history_id}/revert`, { method: "POST" });
        return done;
      },
    });
  }

  return (
    <div className="section" style={{ marginTop: 4 }}>
      <div className="section-head">
        <h2>History</h2>
        <span className="sub">Every add, edit and delete in the activity log, including changes made outside this app</span>
      </div>
      {error && <div className="banner">Couldn't load history: {error}</div>}
      <div className="card">
        {history.length ? (
          <ul className="hist">
            {history.map(e => {
              const row = e.new || e.old;
              const rv = revertInfo(e, data.activities, revertedIds);
              return (
                <li key={e.history_id}>
                  <div className="hist-head">
                    <span className="act-tag" data-a={e.action}>
                      {ACTION_LABEL[e.action]}
                    </span>
                    <span className="title">{row.actual_session}</span>
                    <span className="id num">#{e.activity_id}</span>
                    <time dateTime={e.changed_at} className="num">
                      {fmtStamp(e.changed_at)}
                    </time>
                    <span className="grow" />
                    {rv.ok ? (
                      <button className="btn small" onClick={() => startRevert(e)}>
                        {rv.label}
                      </button>
                    ) : (
                      <span className="rev-note">{rv.note}</span>
                    )}
                  </div>
                  {e.reverts && <div className="rev-note">↩ Reverted the change from {stampOf(e.reverts)}</div>}
                  {e.action === "UPDATE" ? <DiffRows changes={e.changes} /> : <div className="hist-sub">{summarize(row)}</div>}
                </li>
              );
            })}
          </ul>
        ) : (
          <div className="empty">
            No changes recorded yet. From now on, every add, edit and delete in the activity log shows up here.
          </div>
        )}
      </div>
    </div>
  );
}
