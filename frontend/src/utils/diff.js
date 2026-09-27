/** Comparing activities field by field (the History tab and the edit dialog show the result). */

export const FIELDS = [
  ["activity_date", "Date"],
  ["category", "Category"],
  ["actual_session", "Session"],
  ["distance_mi", "Distance"],
  ["duration_min", "Duration"],
  ["output_kj", "Output"],
  ["plan_id", "Plan link"],
  ["notes", "Notes"],
];
export const FIELD_LABEL = Object.fromEntries(FIELDS);

/** Treat "" and null/undefined as the same "empty" value when comparing. */
export const norm = v => (v === "" || v == null ? null : v);

export const diffActivity = (a, b) =>
  FIELDS.filter(([k]) => norm(a[k]) !== norm(b[k])).map(([k]) => ({ field: k, old: norm(a[k]), new: norm(b[k]) }));
