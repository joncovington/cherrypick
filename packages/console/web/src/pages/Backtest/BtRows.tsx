import type { ReactNode } from "react";

/**
 * The backtest tabs' rows (2026-11-07): the cards-or-rows switch every trade list has, on the
 * backtest boards. A tile here is already a summary that opens a lightbox, so a row is the same
 * summary on one line and opens the same lightbox (a row opening its tile in place would only add a
 * click). Each tab maps its own tile model to a row, so a row and its tile cannot disagree.
 *
 * Four cells: `lead` (the status word or chip the tile heads with), `title`, `meta` (the tile's
 * small print) and `end` (age, size or the wait). On a phone a row folds to lead and end, then the
 * title, then the meta.
 */

export type BtRow = {
  key: string;
  label: string;
  lead: ReactNode;
  title: ReactNode;
  meta?: ReactNode;
  end?: ReactNode;
  /** Failed, stopped or missing: drawn with the tile's attention edge. */
  attention?: boolean;
  /** The tile's data attribute, so a test or a link finds the row the way it finds the tile. */
  data?: Record<`data-${string}`, string>;
  onOpen: () => void;
};

export function BtRows({ rows }: { rows: BtRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="tr-list bt-rows" role="list">
      {rows.map((r) => (
        <div key={r.key} role="listitem" className={r.attention ? "tr-item bt-item is-attn" : "tr-item bt-item"}>
          <button type="button" className="bt-row" aria-label={r.label} onClick={r.onOpen} {...r.data}>
            <span className="bt-row-lead">{r.lead}</span>
            <span className="bt-row-title">{r.title}</span>
            <span className="bt-row-meta">{r.meta}</span>
            <span className="bt-row-end">{r.end}</span>
            <span className="bt-row-open" aria-hidden="true">
              ›
            </span>
          </button>
        </div>
      ))}
    </div>
  );
}
