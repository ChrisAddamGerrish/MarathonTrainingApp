import { useEffect, useState } from "react";
import { useData } from "../contexts/DataContext";
import { api } from "../services/api";
import { fmtMi, fmtSeconds, isRun, paceOf } from "../utils/format";
import { decodePolyline, routePath } from "../utils/polyline";
import HeartRateZones from "./HeartRateZones";

const ROUTE_W = 240;
const ROUTE_H = 160;

function Stat({ label, children }) {
  if (children == null || children === "" || children === false) return null;
  return (
    <div>
      <dt>{label}</dt>
      <dd className="num">{children}</dd>
    </div>
  );
}

const joined = (...parts) => parts.filter(Boolean).join(" / ") || null;

/** Everything Strava told us about a logged activity, as a collapsed pane (under each workout in the
 * week view, and in the edit dialog). Its details load the first time it's opened. */
export default function StravaDetails({ activity }) {
  const { data } = useData();
  const [open, setOpen] = useState(false);
  const [details, setDetails] = useState(null);
  const [error, setError] = useState(null);
  const run = isRun(activity.category);

  useEffect(() => {
    if (!open || details) return;
    api(`/api/activities/${activity.activity_id}/strava`).then(setDetails, e => setError(e.message));
  }, [open, details, activity.activity_id]);

  const d = details;
  const route = d?.polyline && !d.trainer ? routePath(decodePolyline(d.polyline), ROUTE_W, ROUTE_H) : "";
  const zoneMinutes = d?.hr_zone_seconds?.map(s => s / 60);

  return (
    <details className="strava-details" onToggle={e => setOpen(e.currentTarget.open)}>
      <summary>From Strava</summary>
      {error && <div className="form-error">{error}</div>}
      {!d && !error && <div className="muted">Loading…</div>}
      {d && (
        <>
          {!d.details_fetched_at && (
            <p className="muted small">Splits, laps and best efforts come with a later sync.</p>
          )}
          <dl className="stat-grid">
            <Stat label="Started">{d.start_time}</Stat>
            <Stat label="Elapsed">
              {d.elapsed_min != null && `${fmtSeconds(d.elapsed_min * 60)}${activity.duration_min ? ` (${fmtSeconds(activity.duration_min * 60)} moving)` : ""}`}
            </Stat>
            <Stat label="Elevation gain">{d.elevation_gain_ft > 0 && `${d.elevation_gain_ft} ft`}</Stat>
            <Stat label="Heart rate">{joined(d.avg_hr && `${d.avg_hr} avg`, d.max_hr && `${d.max_hr} max`)}</Stat>
            <Stat label="Cadence">{d.avg_cadence != null && `${d.avg_cadence} ${run ? "spm" : "rpm"}`}</Stat>
            <Stat label="Power">
              {joined(d.avg_watts && `${d.avg_watts} W avg`, d.weighted_avg_watts && `${d.weighted_avg_watts} W weighted`, d.max_watts && `${d.max_watts} W max`)}
            </Stat>
            <Stat label={run ? "Pace" : "Speed"}>
              {d.avg_speed_mph > 0 &&
                (run
                  ? joined(`${paceOf(3600, d.avg_speed_mph)} avg`, d.max_speed_mph > 0 && `${paceOf(3600, d.max_speed_mph)} best`)
                  : joined(`${d.avg_speed_mph} mph avg`, d.max_speed_mph > 0 && `${d.max_speed_mph} mph max`))}
            </Stat>
            <Stat label="Relative Effort">{d.suffer_score || null}</Stat>
            <Stat label="Calories">{d.calories}</Stat>
            <Stat label="PRs">{d.pr_count || null}</Stat>
            <Stat label={run ? "Shoes" : "Gear"}>{d.gear_name}</Stat>
            <Stat label="Where">{d.trainer == null ? null : d.trainer ? "Indoors" : "Outdoors"}</Stat>
            <Stat label="Device">{d.device_name}</Stat>
          </dl>
          {d.description && <p className="strava-description">{d.description}</p>}
          {zoneMinutes && <HeartRateZones minutes={zoneMinutes} zones={data.hr_zones} />}
          {route && (
            <svg className="route" viewBox={`0 0 ${ROUTE_W} ${ROUTE_H}`} role="img" aria-label="Route map">
              <path d={route} />
            </svg>
          )}
          {run && d.splits?.length > 0 && (
            <table className="data compact num">
              <caption>Mile splits</caption>
              <thead>
                <tr>
                  <th>Mile</th>
                  <th className="r">Pace</th>
                  <th className="r">Elev</th>
                  <th className="r">HR</th>
                </tr>
              </thead>
              <tbody>
                {d.splits.map(s => (
                  <tr key={s.mile}>
                    <td>{s.distance_mi < 0.95 ? `${s.mile} (${fmtMi(s.distance_mi)})` : s.mile}</td>
                    <td className="r">{paceOf(s.moving_s, s.distance_mi)}</td>
                    <td className="r">{s.elevation_ft != null ? `${s.elevation_ft > 0 ? "+" : ""}${s.elevation_ft} ft` : "—"}</td>
                    <td className="r">{s.avg_hr ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {d.laps?.length > 1 && (
            <table className="data compact num">
              <caption>Laps</caption>
              <thead>
                <tr>
                  <th>Lap</th>
                  <th className="r">Distance</th>
                  <th className="r">Time</th>
                  <th className="r">{run ? "Pace" : "HR"}</th>
                </tr>
              </thead>
              <tbody>
                {d.laps.map((lap, n) => (
                  <tr key={n}>
                    <td>{lap.name || n + 1}</td>
                    <td className="r">{fmtMi(lap.distance_mi)}</td>
                    <td className="r">{fmtSeconds(lap.moving_s)}</td>
                    <td className="r">{run ? paceOf(lap.moving_s, lap.distance_mi) : (lap.avg_hr ?? "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {d.best_efforts?.length > 0 && (
            <div className="efforts">
              <div className="caption">Best efforts</div>
              {d.best_efforts.map(e => (
                <span key={e.name} className={"effort" + (e.pr_rank === 1 ? " pr" : "")} title={e.pr_rank ? `Your #${e.pr_rank} time at the time` : undefined}>
                  {e.name} <b className="num">{fmtSeconds(e.elapsed_s)}</b>
                  {e.pr_rank === 1 && " PR"}
                </span>
              ))}
            </div>
          )}
          <a className="strava-link" href={`https://www.strava.com/activities/${d.strava_id}`} target="_blank" rel="noreferrer">
            View on Strava
          </a>
        </>
      )}
    </details>
  );
}
