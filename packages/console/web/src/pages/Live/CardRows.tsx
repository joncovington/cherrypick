import { useState, type ReactNode } from "react";
import { fmtCash } from "../../lib/format";
import { tone } from "./manageParts";

/**
 * Rows for a tab whose items are parent cards (a PMCC, a wheel cycle): one line each, drawn in the
 * trade rows' classes so every rows view in the suite reads alike, and a row opens its full card in
 * place. Every figure is the caller's, taken from what its card shows.
 */
export interface CardRow {
  key: string;
  symbol: string;
  shape: string;
  /** Whole-position cash, signed; null draws a dash. */
  cash: number | null;
  label: string;
  after: string;
  chips: ReactNode;
  card: ReactNode;
}

export function CardRows({ rows }: { rows: CardRow[] }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (k: string) => {
    const next = new Set(open);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    setOpen(next);
  };
  return (
    <div className="tr-list" role="list">
      {rows.map((r) => (
        <div key={r.key} role="listitem" className="tr-item">
          <button type="button" className="tr-row" aria-expanded={open.has(r.key)} onClick={() => toggle(r.key)}>
            <span className="tr-caret" aria-hidden="true">
              ▸
            </span>
            <span className="tr-sym">{r.symbol}</span>
            <span className="tr-shape">{r.shape}</span>
            <span className={`tr-cash ${tone(r.cash)}`}>{fmtCash(r.cash)}</span>
            <span className="tr-cap">
              <span className="tr-cap-label">{r.label}</span>
            </span>
            <span className="tr-after">{r.after}</span>
            <span className="tr-chips">{r.chips}</span>
          </button>
          {open.has(r.key) ? <div className="tr-open">{r.card}</div> : null}
        </div>
      ))}
    </div>
  );
}
