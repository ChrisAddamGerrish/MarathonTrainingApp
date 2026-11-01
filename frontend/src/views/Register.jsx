import { useEffect, useState } from "react";
import { BrandMark } from "../components/common";
import { api, jsonRequest } from "../services/api";
import { DAYS, fmtDate } from "../utils/format";

/** The Monday on or after today, as YYYY-MM-DD. */
function nextMonday() {
  const d = new Date();
  d.setDate(d.getDate() + ((8 - d.getDay()) % 7));
  return d.toLocaleDateString("en-CA");
}

const weekday = iso => DAYS[(new Date(iso + "T00:00:00").getDay() + 6) % 7];

/**
 * Creating an account with an invite code: step 1 of 2. The server signs the new account in, and
 * onRegistered receives the session status; step 2 (connecting Strava) is ConnectStrava.
 */
export default function Register({ onRegistered, onCancel }) {
  const [form, setForm] = useState({
    invite: new URLSearchParams(window.location.search).get("invite") || "",
    username: "",
    password: "",
    confirm: "",
    plan_start: nextMonday(),
    weeks: 20,
    build: true,
    longest_run_mi: "",
    weekly_mi: "",
    runs_per_week: "4",
  });
  const [limits, setLimits] = useState(null); // the safe minimum for the longest run entered
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    document.title = "Create account · Marathon Training";
  }, []);

  const set = key => e => setForm(f => ({ ...f, [key]: e.target.value }));
  const longest = parseFloat(form.longest_run_mi);

  useEffect(() => {
    if (!form.build || !(longest >= 0)) return setLimits(null);
    const timer = setTimeout(() => {
      api(`/api/auth/plan-minimum?longest_run_mi=${longest}`)
        .then(setLimits)
        .catch(() => setLimits(null));
    }, 300);
    return () => clearTimeout(timer);
  }, [form.build, longest]);
  const startDay = form.plan_start ? weekday(form.plan_start) : null;
  const weeks = parseInt(form.weeks, 10);
  const raceDay = (() => {
    if (!form.plan_start || !(weeks >= 1)) return null;
    const d = new Date(form.plan_start + "T00:00:00");
    d.setDate(d.getDate() + weeks * 7 - 1);
    return d.toLocaleDateString("en-CA");
  })();

  function planNote() {
    if (startDay && startDay !== "Mon") return `${fmtDate(form.plan_start)} is a ${startDay}; pick a Monday.`;
    if (!raceDay) return "";
    const race = fmtDate(raceDay, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
    if (!form.build) return `Your plan starts empty: ${weeks} weeks ending ${race}. Add sessions on the Plan page, or import a plan CSV.`;
    if (!limits) return `Race day: ${race}. Tell us where you are now to build the plan.`;
    if (longest < limits.min_long_run_mi || parseFloat(form.weekly_mi) < limits.min_weekly_mi)
      return `A marathon plan needs a running base first: a run of at least ${limits.min_long_run_mi} mi and about ${limits.min_weekly_mi} mi a week.`;
    if (weeks < limits.min_weeks)
      return `From a ${longest} mi longest run, building up safely takes at least ${limits.min_weeks} weeks. Add weeks or start empty.`;
    return `Race day: ${race}. ${weeks} weeks, building to a 20 mi long run 3 weeks out. Check with a doctor first if you have any health concerns.`;
  }

  async function submit(e) {
    e.preventDefault();
    if (form.password !== form.confirm) return setError("The passwords don't match.");
    if (startDay !== "Mon") return setError("The plan has to start on a Monday.");
    setBusy(true);
    setError(null);
    try {
      const { confirm, build, longest_run_mi, weekly_mi, runs_per_week, ...body } = form;
      const goal = build
        ? { longest_run_mi: longest, weekly_mi: parseFloat(weekly_mi), runs_per_week: parseInt(runs_per_week, 10) }
        : null;
      onRegistered(await api("/api/auth/register", jsonRequest("POST", { ...body, weeks, goal })));
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="card login-card" onSubmit={submit}>
        <div className="brand">
          <BrandMark />
          Marathon Training
        </div>
        <div>
          <div className="step">Step 1 of 2</div>
          <h1>Create your account</h1>
        </div>
        <label className="field">
          <span>Invite code</span>
          <input
            autoComplete="off"
            autoCapitalize="none"
            spellCheck="false"
            required
            autoFocus={!form.invite}
            value={form.invite}
            onChange={set("invite")}
          />
        </label>
        <label className="field">
          <span>Username</span>
          <input
            autoComplete="username"
            autoCapitalize="none"
            spellCheck="false"
            required
            maxLength={40}
            pattern="[A-Za-z0-9._\-]+"
            title="Letters, digits, dots, dashes and underscores"
            autoFocus={!!form.invite}
            value={form.username}
            onChange={set("username")}
          />
        </label>
        <label className="field">
          <span>Password (12+ characters)</span>
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={12}
            value={form.password}
            onChange={set("password")}
          />
        </label>
        <label className="field">
          <span>Password again</span>
          <input type="password" autoComplete="new-password" required value={form.confirm} onChange={set("confirm")} />
        </label>
        <div className="grid2">
          <label className="field">
            <span>Plan starts (a Monday)</span>
            <input type="date" required value={form.plan_start} onChange={set("plan_start")} />
          </label>
          <label className="field">
            <span>Weeks</span>
            <input type="number" min={1} max={52} required value={form.weeks} onChange={set("weeks")} />
          </label>
        </div>
        <label className="remember">
          <input type="radio" name="build" checked={form.build} onChange={() => setForm(f => ({ ...f, build: true }))} />
          Build my plan from where I am now
        </label>
        <label className="remember">
          <input
            type="radio"
            name="build"
            checked={!form.build}
            onChange={() => setForm(f => ({ ...f, build: false }))}
          />
          Start with an empty plan (I'll add sessions or import a CSV)
        </label>
        {form.build && (
          <>
            <div className="grid2">
              <label className="field">
                <span>Longest run, last 4 weeks (mi)</span>
                <input
                  type="number"
                  inputMode="decimal"
                  min={0}
                  max={50}
                  step={0.5}
                  required
                  value={form.longest_run_mi}
                  onChange={set("longest_run_mi")}
                />
              </label>
              <label className="field">
                <span>Miles a week now</span>
                <input
                  type="number"
                  inputMode="decimal"
                  min={0}
                  max={200}
                  step={0.5}
                  required
                  value={form.weekly_mi}
                  onChange={set("weekly_mi")}
                />
              </label>
            </div>
            <label className="field">
              <span>Runs a week</span>
              <select value={form.runs_per_week} onChange={set("runs_per_week")}>
                {[3, 4, 5].map(n => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        <p className="muted small-note">{planNote()}</p>
        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}
        <button className="btn primary" type="submit" disabled={busy}>
          {busy ? "Creating account…" : "Create account"}
        </button>
        <button className="btn ghost" type="button" onClick={onCancel}>
          I already have an account
        </button>
      </form>
    </div>
  );
}
