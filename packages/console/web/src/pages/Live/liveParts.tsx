import type { ReactNode } from "react";
import { useOpenParam } from "../Backtest/CardLightbox";
import { BoxDialog, type Box } from "./PortfolioBox";
import { PortfolioBoxContext } from "./portfolioBoxContext";

/**
 * The Live tabs' shared parts (2026-11-06 redesign): a state line, a meter, and the lightboxes a parent
 * card opens. They sit beside the portfolio tab's own (PortfolioLayout, PortfolioBox, TradeCard) and
 * reuse its classes, so every Live tab reads alike.
 */

/** A line that states how things stand: red when it puts a figure in doubt, amber for a caution, grey
 *  for a note. It is the page's reading, not an explanation: a method goes behind the card's ?. */
export function StateLine({ tone = "note", title, children }: { tone?: "bad" | "warn" | "note"; title?: string; children: ReactNode }) {
  return (
    <p className={`lv-state${tone === "bad" ? " is-bad" : tone === "warn" ? " is-warn" : ""}`} title={title}>
      {children}
    </p>
  );
}

/** A share of a limit, 0-100, as a bar; amber past `warnAt`. */
export function Meter({ pct, warnAt = 80, title }: { pct: number; warnAt?: number; title?: string }) {
  const w = Math.max(0, Math.min(100, pct));
  return (
    <div className={`pf-meter${w >= warnAt ? " lv-meter-warn" : ""}`} title={title}>
      <span style={{ width: `${w}%` }} />
    </div>
  );
}

/**
 * The lightboxes of one Live tab, named in the URL (`?box=<id>`): a parent card's `BoxButton` opens
 * one, the back button closes it, ← → step through the tab's boxes. The buttons come from
 * portfolioBoxContext, so a card renders alone (a test) with none.
 */
export function LiveBoxes({ boxes, param = "box", children }: { boxes: Box[]; param?: string; children: ReactNode }) {
  const lb = useOpenParam(param);
  const ids = new Set(boxes.map((b) => b.id));
  const index = boxes.findIndex((b) => b.id === lb.open);
  return (
    <PortfolioBoxContext.Provider value={{ open: lb.show, has: (id) => ids.has(id) }}>
      {children}
      {index >= 0 ? <BoxDialog boxes={boxes} index={index} onStep={(to) => lb.step(boxes[to]?.id ?? "")} onClose={lb.close} /> : null}
    </PortfolioBoxContext.Provider>
  );
}
