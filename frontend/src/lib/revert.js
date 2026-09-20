import { norm } from "./diff";

/**
 * What the Revert button on a history entry can do right now.
 * Returns { ok: false, note } when there is nothing to do, otherwise { ok: true, label, ... }.
 * For edits, `changes` is current -> restored, limited to the fields that edit touched.
 */
export function revertInfo(entry, activities, revertedIds) {
  if (revertedIds.has(entry.history_id)) return { ok: false, note: "Reverted" };
  const cur = activities.find(a => a.activity_id === entry.activity_id);
  if (entry.action === "INSERT") return cur ? { ok: true, label: "Undo add", cur } : { ok: false, note: "Already removed" };
  if (entry.action === "DELETE") return cur ? { ok: false, note: "Already restored" } : { ok: true, label: "Restore" };
  if (!cur) return { ok: false, note: "Activity deleted since" };

  const remaining = entry.changes.filter(c => norm(cur[c.field]) !== norm(c.old));
  if (!remaining.length) return { ok: false, note: "Already at these values" };
  // Fields that were edited again after this entry (neither the old nor the new value).
  const stale = entry.changes.filter(
    c => norm(cur[c.field]) !== norm(c.new) && norm(cur[c.field]) !== norm(c.old),
  ).length;
  return {
    ok: true,
    label: "Revert",
    cur,
    stale,
    changes: remaining.map(c => ({ field: c.field, old: norm(cur[c.field]), new: norm(c.old) })),
  };
}
