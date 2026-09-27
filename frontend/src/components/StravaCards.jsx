import { useState } from "react";
import { useData } from "../contexts/DataContext";
import { useToast } from "../contexts/ToastContext";
import { api, jsonRequest } from "../services/api";
import { fmtDate, fmtSeconds, paceOf } from "../utils/format";
import { workoutHref } from "../utils/links";
import { ProgressBar } from "./common";

const METERS_PER_MILE = 1609.344;

/** Fastest time at each of Strava's best-effort distances, across the logged runs. */
export function PersonalBests({ bests }) {
  return (
    <div className="card strava-card">
      <h3>Personal bests</h3>
      <table className="data compact num">
        <tbody>
          {bests.map(b => (
            <tr key={b.name}>
              <td>{b.name}</td>
              <td className="r">
                <b>{fmtSeconds(b.elapsed_s)}</b>
              </td>
              <td className="r muted">{b.distance_m ? paceOf(b.elapsed_s, b.distance_m / METERS_PER_MILE) : ""}</td>
              <td className="r">
                <a href={workoutHref(b.activity_id)} title={b.session}>
                  {fmtDate(b.date, { month: "short", day: "numeric" })}
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ShoeRow({ shoe }) {
  const { reload } = useData();
  const toast = useToast();
  const [limit, setLimit] = useState(shoe.replace_at_mi ?? "");

  async function save() {
    const miles = limit === "" ? null : Number(limit);
    if (miles === shoe.replace_at_mi) return;
    try {
      await api(`/api/strava/gear/${encodeURIComponent(shoe.gear_id)}`, jsonRequest("PUT", { replace_at_mi: miles }));
      toast(miles ? `${shoe.name}: replace at ${miles} mi` : `${shoe.name}: no replacement reminder`);
      await reload();
    } catch (e) {
      toast(e.message, true);
      setLimit(shoe.replace_at_mi ?? "");
    }
  }

  const near = shoe.replace_at_mi && shoe.distance_mi >= shoe.replace_at_mi * 0.9;
  return (
    <li className={shoe.replace_due ? "due" : ""}>
      <div className="shoe-head">
        <b>{shoe.name}</b>
        {shoe.is_primary && <span className="extra-tag">Default</span>}
        {shoe.replace_due ? <span className="extra-tag warn">Time for a new pair</span> : near && <span className="extra-tag">Replace soon</span>}
        <span className="spacer" />
        <span className="num">{Math.round(shoe.distance_mi)} mi</span>
      </div>
      {shoe.replace_at_mi ? <ProgressBar actual={shoe.distance_mi} planned={shoe.replace_at_mi} /> : null}
      <label className="shoe-limit">
        Replace at
        <input
          type="number"
          min="1"
          step="25"
          inputMode="numeric"
          value={limit}
          placeholder="—"
          onChange={e => setLimit(e.target.value)}
          onBlur={save}
          onKeyDown={e => e.key === "Enter" && e.currentTarget.blur()}
        />
        mi
      </label>
    </li>
  );
}

/** Running shoes from Strava with their mileage, and when each is due for replacing. */
export function Shoes({ gear }) {
  const shoes = gear.filter(g => g.kind === "shoe" && !g.retired);
  if (!shoes.length) return null;
  return (
    <div className="card strava-card">
      <h3>Shoes</h3>
      <ul className="shoes">
        {shoes.map(s => (
          <ShoeRow key={s.gear_id} shoe={s} />
        ))}
      </ul>
      <p className="muted small">Mileage is Strava's total for each pair.</p>
    </div>
  );
}
