import { Fragment } from "react";
import type { TimeSpreadStructure } from "@console/shared";
import { fmtCash } from "../lib/format";

/**
 * The time-spread scanner's structures (docs/time-spread-scanner-plan.md), for the Screeners tab and
 * the Live portfolio card. Every figure is the producers' and MODELLED at each leg's own IV; the debit
 * is per share (`db`), the profit columns whole-position dollars for one unit. Rows come in the
 * producers' order: by the front/back IV ratio, an untested hypothesis. The second line holds the
 * levels a person manages the trade by, so the row stays narrow.
 */

export function per(v: number | null): string {
  return v === null ? "—" : `${v.toFixed(2)} db`;
}

export function pct(v: number | null): string {
  return v === null ? "—" : `${Math.round(v * 100)}%`;
}

function px(v: number | null): string {
  return v === null ? "none" : `$${v.toFixed(2)}`;
}

export function bandText(b: [number | null, number | null] | null): string {
  return b === null ? "—" : `${px(b[0])} / ${px(b[1])}`;
}

export function TimeSpreadTable({ rows, sized = false }: { rows: TimeSpreadStructure[]; sized?: boolean }) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>name</th>
          <th>structure</th>
          <th title="front IV / back IV: the term spread the trade sells">IV front/back</th>
          <th title="per share at the mid, and at the natural fill">debit mid / nat</th>
          <th title="modelled, one unit">θ/day</th>
          <th title="modelled, shares">Δ</th>
          <th title="modelled profit at the short's expiry, one unit: stock unchanged / up 5% / down 10%">
            at short expiry: flat / +5% / −10%
          </th>
          {sized && <th title="the quantity that fits, and the cap that stops the next unit">fits</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <Fragment key={`${r.underlying}|${r.label}`}>
            <tr>
              <td>
                {r.underlying}
                {r.heldName && <span className="chip" title="the book already holds this name"> held</span>}
                {!r.heldName && r.heldSector && (
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
              {sized && (
                <td>
                  {r.units ?? 0}
                  {r.binding ? <span className="muted"> · {r.binding}</span> : null}
                </td>
              )}
            </tr>
            <tr className="row-secondary">
              <td />
              <td colSpan={sized ? 7 : 6} className="muted">
                breakeven {bandText(r.breakeven)} · price stop (close if it closes beyond) {bandText(r.priceStop)} · take profit{" "}
                {per(r.takeProfit).replace(" db", " cr")} · value stop {per(r.valueStop).replace(" db", " cr")} · roll by {r.rollBy ?? "—"}
                {r.gap !== null && r.debitShareOfGap !== null ? ` · ${pct(r.debitShareOfGap)} of the ${r.gap} gap` : ""}
                {` · max loss ${fmtCash(r.maxLoss === null ? null : -r.maxLoss)}`}
                {sized && r.bpAfterPct !== null ? ` · buying power after ${pct(r.bpAfterPct)}` : ""}
              </td>
            </tr>
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}
