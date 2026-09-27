import { useEffect, useState } from "react";
import { BrandMark } from "../components/common";
import { api } from "../services/api";

/**
 * Step 2 of 2 of registering, and what a signed-in account sees until it's connected to Strava
 * (the app can't be used without a connection; see backend/app/api/deps.py).
 */
export default function ConnectStrava({ user, onSignOut }) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    document.title = "Connect Strava · Marathon Training";
    api("/api/strava/status").then(setStatus, e => setError(e.message));
  }, []);

  return (
    <div className="login">
      <div className="card login-card">
        <div className="brand">
          <BrandMark />
          Marathon Training
        </div>
        <div>
          <div className="step">Step 2 of 2</div>
          <h1>Connect your Strava account</h1>
        </div>
        <p className="muted">
          Signed in as <b>{user}</b>. Your workouts are imported from Strava, so connect your account to finish setting
          up. You'll approve access on Strava's site and come straight back here.
        </p>
        {status && !status.configured && (
          <p className="form-error" role="alert">
            Strava isn't set up on this server yet. Ask the person who runs it to add the Strava app's settings.
          </p>
        )}
        {(status?.last_error || error) && (
          <p className="form-error" role="alert">
            {status?.last_error || error}
          </p>
        )}
        <a
          className={"btn strava-connect" + (status?.configured ? "" : " disabled")}
          href={status?.configured ? "/api/strava/connect" : undefined}
          aria-disabled={!status?.configured}
        >
          Connect with Strava
        </a>
        <button className="btn ghost" type="button" onClick={onSignOut}>
          Sign out
        </button>
      </div>
    </div>
  );
}
