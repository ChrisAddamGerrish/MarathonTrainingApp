export const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const parseDate = iso => new Date(iso + "T00:00:00");

export const fmtDate = (iso, opts = { weekday: "short", month: "short", day: "numeric" }) =>
  parseDate(iso).toLocaleDateString(undefined, opts);

export const fmtMi = x => (x == null ? "—" : `${+Number(x).toFixed(2)} mi`);

export const fmtMin = m => {
  if (m == null) return "—";
  m = Math.round(m);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
};

export const fmtSigned = (x, unit) => (x === 0 ? "" : `${x > 0 ? "+" : "−"}${+Math.abs(x).toFixed(2)} ${unit}`);

export const pace = (mi, min) => {
  if (!mi || !min) return "";
  const p = min / mi;
  const m = Math.floor(p);
  const s = Math.round((p - m) * 60);
  return s === 60 ? `${m + 1}:00 /mi` : `${m}:${String(s).padStart(2, "0")} /mi`;
};

export const isRun = c => c === "Run" || c === "Race";

// A logged activity can be a Stretch; a planned session can't (the plan table doesn't allow it).
export const PLAN_CATEGORIES = ["Run", "Bike", "Strength", "Row", "Race", "Rest"];
export const CATEGORIES = [...PLAN_CATEGORIES.slice(0, 3), "Stretch", ...PLAN_CATEGORIES.slice(3)];

export const STATUS = {
  done: { icon: "✓", label: "Done" },
  missed: { icon: "✕", label: "Missed" },
  today: { icon: "●", label: "Today" },
  upcoming: { icon: "○", label: "Upcoming" },
  optional: { icon: "◌", label: "Optional" },
  skipped: { icon: "↷", label: "Skipped" },
  rest: { icon: "", label: "Rest" },
};

/** "3.34 mi · 30 min · 8:59 /mi · 605 kJ" for an activity row. */
export const activityStats = a =>
  [
    a.distance_mi ? fmtMi(a.distance_mi) : null,
    a.duration_min ? fmtMin(a.duration_min) : null,
    a.distance_mi && isRun(a.category) ? pace(a.distance_mi, a.duration_min) : null,
    a.output_kj ? `${+a.output_kj.toFixed(0)} kJ` : null,
  ]
    .filter(Boolean)
    .join(" · ");

/** Week shown on the dashboard: the current one, clamped into the plan. */
export const focusWeek = summary => Math.min(Math.max(summary.current_week, 1), summary.last_week);

/** 1:23:45 or 23:45 from seconds. */
export const fmtSeconds = s => {
  if (s == null) return "—";
  s = Math.round(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
};

/** "8:59 /mi" from seconds taken over miles. */
export const paceOf = (seconds, miles) => (seconds && miles ? `${fmtSeconds(seconds / miles)} /mi` : "—");
