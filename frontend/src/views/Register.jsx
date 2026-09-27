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
  });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    document.title = "Create account · Marathon Training";
  }, []);

  const set = key => e => setForm(f => ({ ...f, [key]: e.target.value }));
  const startDay = form.plan_start ? weekday(form.plan_start) : null;
  const weeks = parseInt(form.weeks, 10);
  const raceDay = (() => {
    if (!form.plan_start || !(weeks >= 1)) return null;
    const d = new Date(form.plan_start + "T00:00:00");
    d.setDate(d.getDate() + weeks * 7 - 1);
    return d.toLocaleDateString("en-CA");
  })();

  async function submit(e) {
    e.preventDefault();
    if (form.password !== form.confirm) return setError("The passwords don't match.");
    if (startDay !== "Mon") return setError("The plan has to start on a Monday.");
    setBusy(true);
    setError(null);
    try {
      const { confirm, ...body } = form;
      onRegistered(await api("/api/auth/register", jsonRequest("POST", { ...body, weeks })));
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
        <p className="muted small-note">
          {startDay && startDay !== "Mon"
            ? `${fmtDate(form.plan_start)} is a ${startDay}; pick a Monday.`
            : raceDay
              ? `Your plan starts empty: ${weeks} weeks ending ${fmtDate(raceDay, { weekday: "short", month: "short", day: "numeric", year: "numeric" })}. Add sessions on the Plan page, or import a plan CSV.`
              : ""}
        </p>
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
