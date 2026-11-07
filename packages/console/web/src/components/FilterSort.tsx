import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { getPref, usePrefsVersion, writePref } from "../lib/prefs";

/**
 * Filter & sort (2026-11-06): what narrows or orders a card's list sits behind one button in the card
 * head, which counts what is not at its default, and a summary line under the head says what is on, so
 * a hidden floor is never invisible. The panel opens under the button, or as a sheet on a phone; Esc,
 * Done or a click outside closes it and focus goes back to the button.
 *
 * Sorts are on size, timing and fit, never on credit or yield: with no tested edge, sorting by those
 * would read as a ranking the evidence does not support.
 */

export interface SortOption<T> {
  id: string;
  label: string;
  /** The order it gives, said in the panel: "soonest first". */
  hint: string;
  /** The row's key, ascending in the hint's order; null sorts last either way. */
  key: (row: T) => number | string | null;
}

export interface SortChoice {
  id: string;
  /** Reversed from the option's own order. */
  reversed: boolean;
}

/** Order rows by an option; rows whose key is null go last in both directions, ties keep their order. */
export function sortRows<T>(rows: T[], option: SortOption<T> | undefined, reversed: boolean): T[] {
  if (!option) return rows;
  return rows
    .map((row, i) => ({ row, i, k: option.key(row) }))
    .sort((a, b) => {
      if (a.k === null && b.k === null) return a.i - b.i;
      if (a.k === null) return 1;
      if (b.k === null) return -1;
      const c = a.k < b.k ? -1 : a.k > b.k ? 1 : 0;
      return (reversed ? -c : c) || a.i - b.i;
    })
    .map((x) => x.row);
}

/** A card's sort, remembered per card as a display preference (`sort.<card>`). */
export function useSortChoice(card: string, fallback: string): [SortChoice, (c: SortChoice) => void] {
  usePrefsVersion();
  const raw = getPref(`sort.${card}`);
  const choice =
    raw && typeof raw === "object" && typeof (raw as SortChoice).id === "string"
      ? { id: (raw as SortChoice).id, reversed: Boolean((raw as SortChoice).reversed) }
      : { id: fallback, reversed: false };
  return [choice, (c) => void writePref(`sort.${card}`, c)];
}

/** The button in the card head: a sliders icon, its name where there is room, the changed count. */
export function FilterSortButton({ changed, open, onOpen, anchor }: { changed: number; open: boolean; onOpen: () => void; anchor: RefObject<HTMLButtonElement | null> }) {
  return (
    <button
      ref={anchor}
      type="button"
      className="fs-btn"
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label={`Filter and sort${changed ? `, ${changed} changed` : ""}`}
      title="Filter and sort"
      onClick={onOpen}
    >
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
        <path d="M2 4h7M12 4h2M2 12h2M7 12h7" />
        <circle cx="10.5" cy="4" r="1.6" />
        <circle cx="5.5" cy="12" r="1.6" />
      </svg>
      <span className="fs-word">filter &amp; sort</span>
      {changed > 0 ? <span className="fs-badge">{changed}</span> : null}
    </button>
  );
}

/** The line under the head: each part opens the panel; reset shows only when something is changed. */
export function FilterSummary({ parts, note, changed, onOpen, onReset }: { parts: string[]; note?: string | null; changed: number; onOpen: () => void; onReset: () => void }) {
  return (
    <div className="fs-summary">
      {parts.map((p) => (
        <button key={p} type="button" className="fs-part" onClick={onOpen}>
          {p}
        </button>
      ))}
      {note ? <span className="muted">{note}</span> : null}
      {changed > 0 ? (
        <button type="button" className="fs-reset" onClick={onReset}>
          reset
        </button>
      ) : null}
    </div>
  );
}

/** The panel, portalled; under its button on a wide screen, a sheet from the bottom on a phone. */
export function FilterSortPanel({ open, onClose, onReset, anchor, children }: { open: boolean; onClose: () => void; onReset: () => void; anchor: RefObject<HTMLButtonElement | null>; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const labelId = useId();
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const r = anchor.current?.getBoundingClientRect();
      if (!r) return;
      const width = 380;
      setPos({ top: r.bottom + 6, left: Math.max(12, Math.min(r.right - width, window.innerWidth - width - 12)) });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, anchor]);
  useEffect(() => {
    if (!open) return;
    box.current?.focus({ preventScroll: true });
    const button = anchor.current;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      button?.focus({ preventScroll: true });
    };
  }, [open, onClose, anchor]);
  if (!open) return null;
  return createPortal(
    <div className="fs-wrap">
      <button type="button" className="fs-scrim" aria-label="Close" tabIndex={-1} onClick={onClose} />
      <div
        ref={box}
        className="fs-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelId}
        tabIndex={-1}
        style={pos ? { top: pos.top, left: pos.left } : undefined}
      >
        <div className="fs-head">
          <b id={labelId}>Filter and sort</b>
          <button type="button" className="fs-x" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="fs-body">{children}</div>
        <div className="fs-foot">
          <button type="button" className="fs-ghost" onClick={onReset}>
            Reset to defaults
          </button>
          <button type="button" className="fs-done" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** A titled group inside the panel. */
export function FilterGroup({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <div className="fs-group">
      <div className="fs-group-t">
        {title}
        {note ? <span>{note}</span> : null}
      </div>
      {children}
    </div>
  );
}

/** The sort options as radios, with the direction under them. */
export function SortSection<T>({ sorts, choice, fallback, onChange }: { sorts: SortOption<T>[]; choice: SortChoice; fallback: string; onChange: (c: SortChoice) => void }) {
  const current = sorts.find((s) => s.id === choice.id) ?? sorts[0];
  return (
    <FilterGroup title="sort" note={`by ${sorts.find((s) => s.id === fallback)?.label ?? fallback} unless you choose`}>
      <div className="fs-sorts" role="radiogroup" aria-label="Sort">
        {sorts.map((s) => (
          <button key={s.id} type="button" role="radio" aria-checked={s.id === current?.id} className="fs-sort" onClick={() => onChange({ id: s.id, reversed: false })}>
            <span className="fs-dot" aria-hidden="true" />
            <span>
              <span className="fs-sort-name">{s.label}</span>
              <br />
              <span className="fs-sort-hint">{s.hint}</span>
            </span>
            <span className="fs-def">{s.id === fallback ? "default" : ""}</span>
          </button>
        ))}
      </div>
      {current ? (
        <span className="mode-toggle fs-dir" role="group" aria-label="direction">
          <button type="button" className={choice.reversed ? "mode-btn" : "mode-btn active"} aria-pressed={!choice.reversed} onClick={() => onChange({ id: current.id, reversed: false })}>
            {current.hint}
          </button>
          <button type="button" className={choice.reversed ? "mode-btn active" : "mode-btn"} aria-pressed={choice.reversed} onClick={() => onChange({ id: current.id, reversed: true })}>
            reversed
          </button>
        </span>
      ) : null}
    </FilterGroup>
  );
}

/** An on/off filter, as a switch with a line saying what it does. */
export function FilterSwitch({ label, description, on, disabled = false, title, onChange }: { label: string; description: string; on: boolean; disabled?: boolean; title?: string; onChange: (v: boolean) => void }) {
  return (
    <button type="button" role="switch" aria-checked={on} disabled={disabled} className="fs-switch" title={title} onClick={() => onChange(!on)}>
      <span className="fs-track" aria-hidden="true" />
      <span>
        <span className="fs-sw-label">{label}</span>
        <br />
        <span className="fs-sw-desc">{description}</span>
      </span>
    </button>
  );
}
