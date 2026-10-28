import { useLayoutEffect, useRef, useState } from "react";
import { fmtMin } from "../utils/format";
import { jumpTo, weekHref } from "../utils/links";

const H = 280;
const M = { l: 34, r: 6, t: 22, b: 48 };

/** Bar colour for each plan week type (tokens in styles.css). */
export const WEEK_COLORS = {
  Normal: "var(--wk-build)",
  "Step-back": "var(--wk-stepback)",
  Peak: "var(--wk-peak)",
  Taper: "var(--wk-peak)",
  Race: "var(--wk-race)",
};

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

/** Bar segment from y0 (bottom) up to y1, with a rounded top of radius r (0 for square). */
function barPath(x, y0, y1, bw, r) {
  const h = y0 - y1;
  if (h <= 0) return "";
  r = Math.min(r, bw / 2, h);
  if (!r) return `M${x},${y0}V${y1}H${x + bw}V${y0}Z`;
  return `M${x},${y0}V${y1 + r}Q${x},${y1} ${x + r},${y1}H${x + bw - r}Q${x + bw},${y1} ${x + bw},${y1 + r}V${y0}Z`;
}

function Tooltip({ week, left, top, width, started, isNow }) {
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
        <span>Run</span>
        <span className="num">{started ? `${+week.actual_run_mi.toFixed(1)} mi` : "—"}</span>
      </div>
      {started && week.planned_run_mi > week.actual_run_mi && (
        <div className="row">
          <span>{isNow ? "To go" : "Missed"}</span>
          <span className="num">{+(week.planned_run_mi - week.actual_run_mi).toFixed(1)} mi</span>
        </div>
      )}
    </div>
  );
}

/**
 * Planned run miles per week as a ladder coloured by week type, hand-rolled SVG. Started weeks
 * stack the miles run under the planned miles still left; a run over plan marks the plan with a line.
 */
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
  const base = M.t + ph;
  const max = Math.max(...weeks.map(w => Math.max(w.planned_run_mi, w.actual_run_mi)), 5);
  const step = max <= 20 ? 5 : 10;
  const top = Math.ceil(max / step) * step;
  const y = v => M.t + ph - (v / top) * ph;
  const gw = pw / weeks.length;
  const bw = Math.max(3, Math.min(24, gw - 8));
  const labelEvery = gw >= 26 ? 1 : gw >= 16 ? 2 : 5;
  const showValues = gw >= 26;
  const ticks = [];
  for (let v = 0; v <= top; v += step) ticks.push(v);

  const hovered = hover != null ? weeks.find(w => w.week === hover) : null;
  const hoverIndex = hovered ? weeks.indexOf(hovered) : -1;
  const nowIndex = weeks.findIndex(w => w.week === summary.current_week);
  const nowX = M.l + nowIndex * gw + gw / 2;

  return (
    <div id="chart" ref={ref}>
      <svg
        width={W}
        height={H}
        role="img"
        aria-label={`Planned run miles for each of ${weeks.length} plan weeks, coloured by week type, with miles run so far stacked in started weeks. Use the table view for exact values.`}
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
          const x = cx - bw / 2;
          const isNow = w.week === summary.current_week;
          const started = w.week <= summary.current_week;
          const color = WEEK_COLORS[w.week_type] ?? WEEK_COLORS.Normal;
          const planned = w.planned_run_mi;
          const run = w.actual_run_mi;
          const left = started ? planned - run : 0;
          const value = started ? run : planned;
          return (
            <g key={w.week}>
              {isNow && <rect className="now-band" x={x0 + 1} y={M.t - 18} width={gw - 2} height={ph + 18} rx={6} />}
              {started ? (
                <>
                  {left > 0 && (
                    <path className="left" d={barPath(x, y(run) - 2, y(planned), bw, 4)} fill={color} stroke={color} />
                  )}
                  <path d={barPath(x, base, y(run), bw, left > 0 ? 0 : 4)} fill={isNow ? "var(--wk-now)" : color} />
                  {run > planned && planned > 0 && <line className="over" x1={x - 2} x2={x + bw + 2} y1={y(planned)} y2={y(planned)} />}
                </>
              ) : (
                <path d={barPath(x, base, y(planned), bw, 4)} fill={color} />
              )}
              {showValues && value > 0 && (
                <text className={"val" + (isNow ? " strong" : "")} x={cx} y={y(Math.max(planned, value)) - 5} textAnchor="middle">
                  {+value.toFixed(1)}
                </text>
              )}
              {(isNow || w.week % labelEvery === 0 || w.week === 1) && (
                <text className={isNow ? "strong" : ""} x={cx} y={base + 16} textAnchor="middle">
                  {w.week}
                </text>
              )}
              <rect
                className={"hit" + (hover === w.week ? " on" : "")}
                x={x0}
                y={M.t}
                width={gw}
                height={ph + 22}
                onMouseEnter={() => setHover(w.week)}
                onMouseLeave={() => setHover(null)}
                onClick={() => goToWeek(w.week)}
              />
            </g>
          );
        })}
        {(nowIndex < 0 || nowX - M.l > 60) && (
          <text x={M.l} y={H - 6}>
            Week
          </text>
        )}
        {nowIndex >= 0 && (
          <g>
            <path className="marker" d={`M${nowX - 5},${H - 6}L${nowX},${H - 15}L${nowX + 5},${H - 6}Z`} />
            {/* Label on the side with room, so late weeks don't run off the right edge. */}
            {nowX > W - 100 ? (
              <text x={nowX - 10} y={H - 6} textAnchor="end">
                you are here
              </text>
            ) : (
              <text x={nowX + 10} y={H - 6}>
                you are here
              </text>
            )}
          </g>
        )}
      </svg>
      {hovered && (
        <Tooltip
          week={hovered}
          left={M.l + hoverIndex * gw + gw / 2}
          top={y(Math.max(hovered.planned_run_mi, hovered.actual_run_mi)) - 18}
          width={W}
          started={hovered.week <= summary.current_week}
          isNow={hovered.week === summary.current_week}
        />
      )}
    </div>
  );
}
