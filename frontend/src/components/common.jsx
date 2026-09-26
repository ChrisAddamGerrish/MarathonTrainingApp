import { STATUS } from "../utils/format";

export const Chip = ({ category }) => (
  <span className="chip" data-cat={category}>
    <i />
    {category}
  </span>
);

export const StatusPill = ({ status }) => (
  <span className="pill" data-s={status}>
    {STATUS[status].icon} {STATUS[status].label}
  </span>
);

export function ProgressBar({ actual, planned }) {
  const pct = planned > 0 ? Math.min(100, (actual / planned) * 100) : 0;
  return (
    <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Table row that behaves like a button: click, Enter or Space. */
export function ClickableRow({ onActivate, title, children }) {
  return (
    <tr
      className="clickable"
      tabIndex={0}
      title={title}
      onClick={onActivate}
      onKeyDown={e => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onActivate();
        }
      }}
    >
      {children}
    </tr>
  );
}

export const BrandMark = () => (
  <svg viewBox="0 0 32 32" aria-hidden="true">
    <rect width="32" height="32" rx="8" fill="var(--accent)" />
    <path
      d="M8 21l5-9 4 6 3-4 4 7"
      fill="none"
      stroke="#fff"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);
