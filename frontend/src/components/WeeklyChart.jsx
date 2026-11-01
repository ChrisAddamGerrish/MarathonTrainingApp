import { useLayoutEffect, useRef, useState } from "react";
import { fmtMin } from "../utils/format";
import { jumpTo, weekHref } from "../utils/links";

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

const GAP = 2; // px of surface between a bar's long run and its other runs

/** A bar segment from y0 (bottom) up to y1, its top rounded (4px) if it's the bar's data end. */
function segmentPath(x, y0, y1, bw, roundTop) {
  if (y0 - y1 <= 0) return "";
  const r = roundTop ? Math.min(4, bw / 2, y0 - y1) : 0;
  return `M${x},${y0}V${y1 + r}Q${x},${y1} ${x + r},${y1}H${x + bw - r}Q${x + bw},${y1} ${x + bw},${y1 + r}V${y0}Z`;
}

/** A week's bar as [long run, other runs] paths: the long run at the bottom, square at the baseline. */
function stackedPaths(x, total, long, bw, top, ph) {
  const base = M.t + ph;
  const toPx = v => (v / top) * ph;
  const longH = Math.min(toPx(long || 0), toPx(total));
  const totalH = toPx(total);
  const hasOther = totalH - longH > 0.5;
  const longPath = segmentPath(x, base, base - longH, bw, !hasOther);
  const otherBottom = longH > 0 ? base - longH - GAP : base;
  const otherPath = hasOther ? segmentPath(x, otherBottom, Math.min(base - totalH, otherBottom), bw, true) : "";
  return [longPath, otherPath];
}

const other = (total, long) => Math.max(total - (long || 0), 0);
const mi = x => `${+x.toFixed(1)} mi`;

function Split({ long, total, kind }) {
  return (
    <>
      {long != null && (
        <div className="row sub">
          <span>
            <i className="sw" style={{ background: `var(--series-${kind}-long)` }} />
            Long run
          </span>
          <span className="num">{mi(long)}</span>
        </div>
      )}
      <div className="row sub">
        <span>
          <i className="sw" style={{ background: `var(--series-${kind}-other)` }} />
          Other runs
        </span>
        <span className="num">{mi(other(total, long))}</span>
      </div>
    </>
  );
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
        <span className="num">{mi(week.planned_run_mi)}</span>
      </div>
      {week.planned_run_mi > 0 && <Split long={week.planned_long_run_mi} total={week.planned_run_mi} kind="planned" />}
      <div className="row">
        <span>Actual</span>
        <span className="num">{started ? mi(week.actual_run_mi) : "—"}</span>
      </div>
      {started && week.actual_run_mi > 0 && (
        <Split long={week.actual_long_run_mi} total={week.actual_run_mi} kind="actual" />
      )}
    </div>
  );
}

/** Planned vs. actual run miles per week, each bar split into long run and other runs; hand-rolled SVG. */
export default function WeeklyChart({ weeks, summary, showTable }) {
  const [ref, measured] = useElementWidth();
  const [hover, setHover] = useState(null);
  const goToWeek = n => jumpTo(weekHref(n));

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
                <th className="r">Planned long</th>
                <th className="r">Actual mi</th>
                <th className="r">Actual long</th>
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
                    <td className="r">{w.planned_long_run_mi != null ? +w.planned_long_run_mi.toFixed(1) : "—"}</td>
                    <td className="r">{started ? +w.actual_run_mi.toFixed(1) : "—"}</td>
                    <td className="r">
                      {started && w.actual_long_run_mi != null ? +w.actual_long_run_mi.toFixed(1) : "—"}
                    </td>
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
        aria-label={`Planned versus actual run miles for each of ${weeks.length} plan weeks, each split into the long run and the other runs. Use the table view for exact values.`}
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
          const [plannedLong, plannedOther] = stackedPaths(
            cx - bw - 1, w.planned_run_mi, w.planned_long_run_mi, bw, top, ph);
          const [actualLong, actualOther] = stackedPaths(cx + 1, w.actual_run_mi, w.actual_long_run_mi, bw, top, ph);
          return (
            <g key={w.week}>
              {isNow && <rect className="now-band" x={x0 + 1} y={M.t} width={gw - 2} height={ph} rx={6} />}
              <path d={plannedLong} fill="var(--series-planned-long)" />
              <path d={plannedOther} fill="var(--series-planned-other)" />
              {started && <path d={actualLong} fill="var(--series-actual-long)" />}
              {started && <path d={actualOther} fill="var(--series-actual-other)" />}
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
