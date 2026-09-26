import { useCallback, useEffect, useState } from "react";
import { useData } from "../contexts/DataContext";
import { useToast } from "../contexts/ToastContext";
import { api, jsonRequest } from "../services/api";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function ago(iso) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h ago` : new Date(iso).toLocaleString();
}

function describe(result) {
  if (!result) return "";
  const parts = [];
  if (result.created) parts.push(`${plural(result.created, "new workout")} imported`);
  if (result.matched) parts.push(`${result.matched} already logged`);
  if (result.duplicates) parts.push(`${plural(result.duplicates, "duplicate recording")} merged`);
  return parts.length ? parts.join(", ") : "nothing new";
}

/** Strava connection status on the Activity log page: connect, sync now, disconnect. */
export default function StravaPanel() {
  const { reload } = useData();
  const toast = useToast();
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await api("/api/strava/status"));
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function syncNow() {
    setBusy(true);
    try {
      const result = await api("/api/strava/sync", jsonRequest("POST", {}));
      setStatus(result);
      toast(`Strava: ${describe(result.imported)}`);
      if (result.imported.created) reload();
    } catch (e) {
      toast(e.message, true);
      load();
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    if (!window.confirm("Disconnect Strava? Workouts already imported stay in your log.")) return;
    try {
      setStatus(await api("/api/strava/disconnect", jsonRequest("POST", {})));
      toast("Strava disconnected");
    } catch (e) {
      toast(e.message, true);
    }
  }

  if (!status) return null;

  if (!status.configured) {
    return (
      <div className="strava card">
        <span className="strava-mark" aria-hidden="true" />
        <div className="strava-text">
          <b>Strava import isn't set up.</b>{" "}
          <span className="muted">Add your Strava app's ID and secret to strava.env on the server, then restart.</span>
        </div>
      </div>
    );
  }

  if (!status.connected) {
    return (
      <div className="strava card">
        <span className="strava-mark" aria-hidden="true" />
        <div className="strava-text">
          <b>Import workouts from Strava</b>
          <span className="muted"> — then use Sync now to bring in new workouts.</span>
          {status.last_error && <div className="form-error">{status.last_error}</div>}
        </div>
        <a className="btn strava-connect" href="/api/strava/connect">
          Connect with Strava
        </a>
      </div>
    );
  }

  return (
    <div className="strava card">
      <span className="strava-mark" aria-hidden="true" />
      <div className="strava-text">
        <b>Strava{status.athlete ? `: ${status.athlete}` : ""}</b>
        <span className="muted">
          {" "}
          · {status.last_sync ? `last synced ${ago(status.last_sync)} (${describe(status.last_result)})` : "not synced yet"}
        </span>
        {status.last_error && <div className="form-error">Last sync failed: {status.last_error}</div>}
      </div>
      <button className="btn small" onClick={syncNow} disabled={busy}>
        {busy ? "Syncing…" : "Sync now"}
      </button>
      <button className="btn small ghost" onClick={disconnect}>
        Disconnect
      </button>
    </div>
  );
}
