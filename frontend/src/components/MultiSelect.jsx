import { useEffect, useId, useRef, useState } from "react";

/**
 * A filter dropdown where several options can be ticked. Nothing ticked means "all".
 * options: [{ value, label, content? }] (content, if given, is shown in the list instead of label).
 * selected: the ticked values; onChange gets the new list, in the options' order.
 */
export default function MultiSelect({ label, allLabel, noun, options, selected, onChange }) {
  const [open, setOpen] = useState(false);
  const root = useRef(null);
  const trigger = useRef(null);
  const panelId = useId();

  // Close on a click outside, or Escape (handing focus back to the button).
  useEffect(() => {
    if (!open) return;
    const onPointer = e => root.current && !root.current.contains(e.target) && setOpen(false);
    const onKey = e => {
      if (e.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = value =>
    onChange(options.map(o => o.value).filter(v => (v === value ? !selected.includes(v) : selected.includes(v))));

  const chosen = options.filter(o => selected.includes(o.value));
  const summary =
    chosen.length === 0 ? allLabel : chosen.length <= 2 ? chosen.map(o => o.label).join(", ") : `${chosen.length} ${noun}`;

  return (
    <div className="multi" ref={root}>
      <button
        ref={trigger}
        type="button"
        className="multi-trigger"
        aria-label={`${label}: ${summary}`}
        aria-expanded={open}
        aria-controls={panelId}
        data-active={chosen.length > 0 || undefined}
        onClick={() => setOpen(o => !o)}
      >
        {summary}
        <span className="caret" aria-hidden="true">
          ▾
        </span>
      </button>
      {open && (
        <div className="multi-panel" id={panelId} role="group" aria-label={label}>
          <div className="multi-head">
            <span>{chosen.length ? `${chosen.length} selected` : allLabel}</span>
            <button type="button" className="link" onClick={() => onChange([])} disabled={!chosen.length}>
              Clear
            </button>
          </div>
          {options.map(o => (
            <label className="multi-option" key={o.value}>
              <input type="checkbox" checked={selected.includes(o.value)} onChange={() => toggle(o.value)} />
              {o.content ?? o.label}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
