import { Fragment, useState } from "react";
import type { TimeSpreadLeg, TimeSpreadStructure } from "@console/shared";
import { fmtCash } from "../lib/format";
import type { TimeSpreadView } from "../lib/timeSpreadView";
import { bandText, pct, per } from "./TimeSpreadTable";

/**
 * The scan's structures on the Screeners tab (docs/time-spread-scanner-plan.md), each marked with how
 * it stands against the account (`joinAccount`) and openable to the levels a person manages it by.
 * Every figure is the scan's or manage's, MODELLED at each leg's own IV; this formats and decides
 * nothing, and holds no control that stages or sends an order.
 */

function legText(l: TimeSpreadLeg | null, quote: [number | null, number | null] | undefined): string {
  if (!l) return "—";
  const q = quote ? `bid ${quote[0]?.toFixed(2) ?? "—"} / ask ${quote[1]?.toFixed(2) ?? "—"}` : "no quote";
  return `${l.option?.replace(/\s+/g, " ") ?? "—"} (${l.strike ?? "—"}${l.right ?? ""}) ${q}`;
}

/** What the open row shows: the management levels and the two legs, from the scan's own fields. */
export function TimeSpreadDetail({ s }: { s: TimeSpreadStructure }) {
  return (
    <ul className="muted" style={{ margin: 0, paddingLeft: 18 }} aria-label="management levels">
      <li>breakeven band {bandText(s.breakeven)}</li>
      <li>price stop band {bandText(s.priceStop)}: close if the stock closes beyond it</li>
      <li>take profit {per(s.takeProfit).replace(" db", " cr")}</li>
      <li>value stop {per(s.valueStop).replace(" db", " cr")}</li>
      <li>roll by {s.rollBy ?? "—"}</li>
      <li>max loss {fmtCash(s.maxLoss === null ? null : -s.maxLoss)}</li>
      <li>long (back): {legText(s.longLeg, s.entryQuotes?.[0])}</li>
      <li>short (front): {legText(s.shortLeg, s.entryQuotes?.[1])}</li>
    </ul>
  );
}

function FitCell({ v }: { v: TimeSpreadView }) {
  switch (v.state) {
    case "fits":
      return <span className="chip chip-ok" title="sized to the account: this many units fit">fits ×{v.units}</span>;
    case "stopped":
      return <span className="chip chip-warn" title="the cap that stops the first unit">{v.binding ?? "too big"}</span>;
    case "stopped-unlisted":
      return <span className="muted" title="the evaluation stopped this row but itemises only some of them">too big (cap not itemised)</span>;
    case "absent":
      return <span className="muted" title="the evaluation holds time spreads but not this row">not sized</span>;
    default:
      return <span className="muted">—</span>;
  }
}

const COLS = 9;

export function TimeSpreadScanTable({ rows, initiallyOpen = [] }: { rows: TimeSpreadView[]; initiallyOpen?: string[] }) {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set(initiallyOpen));
  const toggle = (k: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th aria-label="open" />
          <th>name</th>
          <th>structure</th>
          <th title="front IV / back IV: the term spread the trade sells">IV front/back</th>
          <th title="per share at the mid, and at the natural fill">debit mid / nat</th>
          <th title="modelled, one unit">θ/day</th>
          <th title="modelled, shares">Δ</th>
          <th title="modelled profit at the short's expiry, one unit: stock unchanged / up 5% / down 10%">
            at short expiry: flat / +5% / −10%
          </th>
          <th title="whether the account's sizing fits it, or the cap that stops it">account</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((v) => {
          const r = v.s;
          const k = `${r.underlying}|${r.label}`;
          const isOpen = open.has(k);
          return (
            <Fragment key={k}>
              <tr>
                <td>
                  <button
                    type="button"
                    className="btn btn-quiet"
                    aria-expanded={isOpen}
                    aria-label={`${isOpen ? "hide" : "show"} levels for ${r.underlying} ${r.label}`}
                    onClick={() => toggle(k)}
                  >
                    {isOpen ? "▾" : "▸"}
                  </button>
                </td>
                <td>
                  {r.underlying}
                  {v.heldName && <span className="chip" title="the book already holds this name"> held</span>}
                  {!v.heldName && v.heldSector && (
                    <span className="chip" title={`the book already holds a name in ${r.sector ?? "this sector"}`}> sector held</span>
                  )}
                </td>
                <td>
                  {r.label} <span className="muted">({r.direction})</span>
                </td>
                <td>
                  {pct(r.frontIv)} / {pct(r.backIv)} <span className="muted">×{r.ratio?.toFixed(2) ?? "—"}</span>
                </td>
                <td>
                  {per(r.debitMid)} / {per(r.debitNatural)}
                </td>
                <td>{fmtCash(r.thetaDay)}</td>
                <td>{r.netDelta?.toFixed(1) ?? "—"}</td>
                <td>
                  {fmtCash(r.pnlFlat)} / {fmtCash(r.pnlUp5)} / {fmtCash(r.pnlDown10)}
                  {r.losesUnchanged && <span className="chip" title="loses with the stock unchanged"> loses flat</span>}
                </td>
                <td>
                  <FitCell v={v} />
                </td>
              </tr>
              {isOpen && (
                <tr className="row-secondary">
                  <td />
                  <td colSpan={COLS - 1}>
                    <TimeSpreadDetail s={r} />
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
