import { useEffect, useState } from "react";
import { BrandMark } from "../components/common";
import { api, jsonRequest } from "../services/api";

/** Sign-in page. onSignedIn receives the session status from the server. */
export default function Login({ configured, expired, onSignedIn, onRegister }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    document.title = "Sign in · Marathon Training";
  }, []);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await api("/api/auth/login", jsonRequest("POST", { username, password, remember })));
    } catch (err) {
      setError(err.message);
      setPassword("");
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
        <h1>Sign in</h1>
        {!configured && (
          <p className="form-error" role="alert">
            Sign-in isn't set up yet. On the server, run start.ps1 -ResetLogin.
          </p>
        )}
        {expired && configured && <p className="muted">Your session ended. Sign in again to continue.</p>}
        <label className="field">
          <span>Username</span>
          <input
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck="false"
            required
            autoFocus
            value={username}
            onChange={e => setUsername(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={e => setPassword(e.target.value)}
          />
        </label>
        <label className="remember">
          <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} />
          Remember me for 90 days
        </label>
        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}
        <button className="btn primary" type="submit" disabled={busy || !configured}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {configured && (
          <button className="btn ghost" type="button" onClick={onRegister}>
            Have an invite code? Create an account
          </button>
        )}
      </form>
    </div>
  );
}
