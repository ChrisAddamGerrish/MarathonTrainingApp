import { useRef } from "react";
import { api } from "../services/api";
import { useData } from "../contexts/DataContext";
import { useDialogs } from "../contexts/DialogContext";
import { useToast } from "../contexts/ToastContext";
import { downloadCsv } from "../utils/csv";

const KINDS = {
  workouts: { noun: "workout", title: "Restore workouts from this backup?", done: "Workouts restored" },
  plan: { noun: "planned session", title: "Restore the training plan from this backup?", done: "Plan restored" },
};

const plural = (n, noun) => `${n} ${noun}${n === 1 ? "" : "s"}`;

const restore = (kind, text, apply) =>
  api(`/api/restore/${kind}${apply ? "?apply=true" : ""}`, {
    method: "POST",
    headers: { "Content-Type": "text/csv" },
    body: text,
  });

/**
 * Export CSV / Import CSV for workouts or the plan. `exportCsv()` returns the file's text (null
 * disables the button). Import asks the server what the file would change and shows that for
 * confirmation before anything is written.
 */
export default function BackupButtons({ kind, exportCsv, exportTitle }) {
  const { data } = useData();
  const { openReview } = useDialogs();
  const toast = useToast();
  const input = useRef(null);
  const { noun, title, done } = KINDS[kind];

  async function pick(e) {
    const file = e.target.files[0];
    e.target.value = ""; // so picking the same file again still fires
    if (!file) return;
    try {
      const text = await file.text();
      const r = await restore(kind, text, false);
      const lines = [
        r.added && `${plural(r.added, noun)} added`,
        r.changed && `${plural(r.changed, noun)} changed`,
        r.removed && `${plural(r.removed, noun)} removed (not in the file)`,
        r.unlinked && `${plural(r.unlinked, "logged workout")} unlinked from removed sessions`,
        r.unchanged && `${plural(r.unchanged, noun)} already match`,
      ].filter(Boolean);
      if (!r.added && !r.changed && !r.removed) {
        toast(`Nothing to restore: the ${kind === "plan" ? "plan" : "log"} already matches ${file.name}`);
        return;
      }
      openReview({
        title,
        danger: r.removed > 0,
        confirmLabel: "Restore",
        body: (
          <>
            <p className="hist-sub" style={{ marginBottom: 12 }}>
              From <b>{file.name}</b>. This replaces your {kind === "plan" ? "training plan" : "activity log"} with the
              file's contents. A copy of the database is saved to the backups folder first.
            </p>
            <ul className="restore-summary">
              {lines.map(l => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </>
        ),
        run: async () => {
          const res = await restore(kind, text, true);
          return `${done}: ${res.added} added, ${res.changed} changed, ${res.removed} removed`;
        },
      });
    } catch (err) {
      toast(`Couldn't import ${file.name}: ${err.message}`, true);
    }
  }

  return (
    <>
      <button
        className="btn"
        disabled={!exportCsv}
        title={exportTitle}
        onClick={() => downloadCsv(`marathon-${kind}-${data.summary.today}.csv`, exportCsv())}
      >
        Export CSV
      </button>
      <button
        className="btn"
        title={`Restore ${kind === "plan" ? "the plan" : "workouts"} from an exported CSV`}
        onClick={() => input.current.click()}
      >
        Import CSV
      </button>
      <input ref={input} type="file" accept=".csv,text/csv" hidden onChange={pick} />
    </>
  );
}
