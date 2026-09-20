import { useLayoutEffect, useRef, useState } from "react";
import { fmtMin } from "../utils/format";

const H = 250;
const M = { l: 34, r: 6, t: 14, b: 28 };

function useElementWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/** Bar with a 4px rounded data end, square at the baseline. */
function barPath(x, val, bw, top, ph) {
  const h = (val / top) * ph;
  if (h <= 0) return "";
  const r = Math.min(4, bw / 2, h);
  const yy = M.t + ph - h;
  const base = M.t + ph;
  return `M${x},${base}V${yy + r}Q${x},${yy} ${x + r},${yy}H${x + bw - r}Q${x + bw},${yy} ${x + bw},${yy + r}V${base}Z`;
}

function Tooltip({ week, left, top, width, started }) {
  const ref = useRef(null);
  // Centre on the hovered week, but keep the tooltip inside the chart.
  useLayoutEffect(() => {
    const half = ref.current.offsetWidth / 2;
    ref.current.style.left = `${Math.min(Math.max(left, half), width - half)}px`;
  }, [left, width]);
  return (
    <div className="tip" ref={ref} style={{ left, top }}>
      <b>
        Week {week.week} · {week.week_type}
      </b>
      <div className="row">
        <span>Planned</span>
        <span className="num">{+week.planned_run_mi.toFixed(1)} mi</span>
      </div>
      <div className="row">
        <span>Actual</span>
        <span className="num">{started ? `${+week.actual_run_mi.toFixed(1)} mi` : "—"}</span>
      </div>
    </div>
  );
}

/** Planned vs. actual run miles per week, hand-rolled SVG. */
export default function WeeklyChart({ weeks, summary, showTable }) {
  const [ref, measured] = useElementWidth();
  const [hover, setHover] = useState(null);
  const goToWeek = n => {
    window.location.hash = `#/plan/${n}`;
  };

  if (showTable) {
    return (
      <div id="chart" ref={ref}>
        <div className="table-scroll">
          <table className="data num">
            <thead>
              <tr>
                <th>Week</th>
                <th>Type</th>
                <th className="r">Planned mi</th>
                <th className="r">Actual mi</th>
                <th className="r">Planned time</th>
                <th className="r">Actual time</th>
              </tr>
            </thead>
            <tbody>
              {weeks.map(w => {
                const started = w.week <= summary.current_week;
                return (
                  <tr className="clickable" key={w.week} onClick={() => goToWeek(w.week)}>
                    <td>{w.week}</td>
                    <td>{w.week_type}</td>
                    <td className="r">{+w.planned_run_mi.toFixed(1)}</td>
                    <td className="r">{started ? +w.actual_run_mi.toFixed(1) : "—"}</td>
                    <td className="r">{fmtMin(w.planned_min)}</td>
                    <td className="r">{started ? fmtMin(w.actual_min) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  const W = measured || 640;
  const pw = W - M.l - M.r;
  const ph = H - M.t - M.b;
  const max = Math.max(...weeks.map(w => Math.max(w.planned_run_mi, w.actual_run_mi)), 5);
  const step = max <= 20 ? 5 : 10;
  const top = Math.ceil(max / step) * step;
  const y = v => M.t + ph - (v / top) * ph;
  const gw = pw / weeks.length;
  const bw = Math.max(3, Math.min(24, (gw - 8) / 2));
  const labelEvery = gw >= 26 ? 1 : gw >= 16 ? 2 : 5;
  const ticks = [];
  for (let v = 0; v <= top; v += step) ticks.push(v);

  const hovered = hover != null ? weeks.find(w => w.week === hover) : null;
  const hoverIndex = hovered ? weeks.indexOf(hovered) : -1;

  return (
    <div id="chart" ref={ref}>
      <svg
        width={W}
        height={H}
        role="img"
        aria-label={`Planned versus actual run miles for each of ${weeks.length} plan weeks. Use the table view for exact values.`}
      >
        {ticks.map(v => (
          <g key={v}>
            <line className="grid" x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} />
            <text x={M.l - 8} y={y(v) + 4} textAnchor="end">
              {v}
            </text>
          </g>
        ))}
        {weeks.map((w, i) => {
          const x0 = M.l + i * gw;
          const cx = x0 + gw / 2;
          const isNow = w.week === summary.current_week;
          const started = w.week <= summary.current_week;
          return (
            <g key={w.week}>
              {isNow && <rect className="now-band" x={x0 + 1} y={M.t} width={gw - 2} height={ph} rx={6} />}
              <path d={barPath(cx - bw - 1, w.planned_run_mi, bw, top, ph)} fill="var(--series-planned)" />
              {started && <path d={barPath(cx + 1, w.actual_run_mi, bw, top, ph)} fill="var(--series-actual)" />}
              {(isNow || w.week % labelEvery === 0 || w.week === 1) && (
                <text className={isNow ? "strong" : ""} x={cx} y={H - 8} textAnchor="middle">
                  {w.week}
                </text>
              )}
              <rect
                className={"hit" + (hover === w.week ? " on" : "")}
                x={x0}
                y={M.t}
                width={gw}
                height={ph + M.b - 14}
                onMouseEnter={() => setHover(w.week)}
                onMouseLeave={() => setHover(null)}
                onClick={() => goToWeek(w.week)}
              />
            </g>
          );
        })}
      </svg>
      {hovered && (
        <Tooltip
          week={hovered}
          left={M.l + hoverIndex * gw + gw / 2}
          top={M.t + 8}
          width={W}
          started={hovered.week <= summary.current_week}
        />
      )}
    </div>
  );
}
