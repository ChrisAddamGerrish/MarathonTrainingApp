/** A stacked bar of time in each heart-rate zone, with a legend. `minutes` is per zone (Z1..Z5);
 * `zones` (optional) are the athlete's [{min, max}] bpm ranges, shown in the tooltips. */
export default function HeartRateZones({ minutes, zones, compact = false }) {
  const total = minutes.reduce((t, m) => t + m, 0);
  if (!total) return null;
  const range = n => {
    const z = zones?.[n];
    if (!z) return "";
    return z.max === -1 ? ` (${z.min}+ bpm)` : ` (${z.min}–${z.max} bpm)`;
  };
  const easy = minutes.slice(0, 2).reduce((t, m) => t + m, 0);
  return (
    <div className={"zones" + (compact ? " compact" : "")}>
      <div className="zone-bar" role="img" aria-label={`Heart-rate zones: ${minutes.map((m, n) => `Z${n + 1} ${Math.round(m)} min`).join(", ")}`}>
        {minutes.map((m, n) =>
          m > 0 ? (
            <span key={n} style={{ width: `${(m / total) * 100}%`, background: `var(--z${n + 1})` }} title={`Z${n + 1}${range(n)}: ${Math.round(m)} min`} />
          ) : null,
        )}
      </div>
      {!compact && (
        <div className="zone-legend">
          {minutes.map((m, n) => (
            <span key={n}>
              <i style={{ background: `var(--z${n + 1})` }} />Z{n + 1} {Math.round(m)}m
            </span>
          ))}
          <span className="muted">· {Math.round((easy / total) * 100)}% easy (Z1–2)</span>
        </div>
      )}
    </div>
  );
}
