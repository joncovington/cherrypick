import { useState } from "react";
import type { ManageBpLevel, ManageCandidate, ManageCandidates } from "@console/shared";
import { Card } from "../../components/DataTable";
import { fmtCash, fmtMoney } from "../../lib/format";

/**
 * Income trades from our own universe, sized to the account (packages/manage `candidates.py`): the
 * ones that fit, with their quantity and the cap the next unit would break, and the ones that do not,
 * with the cap that stopped them and the narrowed version where one fits. Every figure is the
 * package's, in its order (theta per dollar of buying power): a ranking the server made is not
 * re-sorted here. Money follows the suite layout: a credit received is +, whole-position dollars,
 * the one per-share figure is the net price and says so. Priced at mid from the previous afternoon's
 * quotes, and the card says to re-quote before sending.
 */

const KIND: Record<string, string> = {
  short_put: "short put",
  put_spread: "put credit spread",
  call_spread: "call credit spread",
  covered_call: "covered call",
};

const BASIS: Record<string, string> = {
  cash: "a cash account or IRA (puts cash-secured)",
  margin: "a margin account (Reg T)",
  portfolio_margin: "portfolio margin (buying power not modelled)",
};

/** A cap from the evaluation's ruleset as the caption states it (` 25%`), or nothing where the
 *  evaluation embeds no ruleset: a caption naming a number the account is not held to is worse than
 *  one naming none. */
function capPct(v: number | null | undefined): string {
  return v === null || v === undefined ? "" : ` ${Math.round(v * 1000) / 10}%`;
}

/** The underlying, badged when it is a declared daily-reset fund (the Screeners tabs' own badge). */
function Name({ c }: { c: ManageCandidate }) {
  return (
    <>
      {c.underlying}
      {c.leveraged ? (
        <span className="chip" title="A daily-reset fund: it decays and gaps in ways its delta does not price">
          {c.leveraged}
        </span>
      ) : null}
      {c.heldSameExpiry ? (
        <span
          className="chip"
          title="The account already holds an option on this name with this expiry: they settle together, and an opposite side makes one structure (a strangle, an iron fly) no rule judged"
        >
          held · same expiry
        </span>
      ) : null}
      {c.earningsUnknown ? (
        <span
          className="chip chip-warn"
          title="No earnings date was found for this name, so nothing checked that a report falls outside the trade. Check before sending."
        >
          earnings unknown
        </span>
      ) : null}
    </>
  );
}

function legs(c: ManageCandidate): string {
  return c.legs.map((l) => `${l.quantity > 0 ? "+" : "−"}${l.strike}${l.right}`).join(" / ");
}

function price(v: number | null): string {
  return v === null ? "—" : `${Math.abs(v).toFixed(2)} ${v >= 0 ? "cr" : "db"}`;
}

function times(n: number, v: number | null): number | null {
  return v === null ? null : n * v;
}

/** The loss the per-trade cap was applied to: a short put's stress loss, a spread's max loss (the
 *  package records it as both). A covered call's risk is the shares already held. */
function risk(c: ManageCandidate, n: number): string {
  return c.kind === "covered_call" ? "covered" : fmtMoney(times(n, c.perUnit.stressLoss));
}

function narrowed(c: ManageCandidate): string | null {
  if (!c.alternativeTo) return null;
  return c.alternativeTo === c.kind ? "narrowed to fit" : `narrowed from the ${KIND[c.alternativeTo] ?? c.alternativeTo}`;
}

function FitsTable({ rows }: { rows: ManageCandidate[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>name</th>
            <th>trade</th>
            <th>legs</th>
            <th title="contracts (spreads: one of each leg)">qty</th>
            <th title="per-share net price at mid, and the natural fill (sell at the bid, buy at the ask)">price</th>
            <th title="signed cash flow, whole position: a credit received is +">cash</th>
            <th title="a spread's max loss; a short put's loss at the name's 1st-percentile move over the holding period">risk</th>
            <th title="buying power the position ties up">buying power</th>
            <th title="theta, whole position, dollars a day">θ / day</th>
            <th title="beta-weighted delta the position adds, in SPY shares">Δ SPY sh</th>
            <th title="the cap the next contract would break">next stops at</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c, i) => (
            <tr key={`${c.underlying}-${c.kind}-${i}`}>
              <td>
                <Name c={c} />
              </td>
              <td>
                {KIND[c.kind] ?? c.kind}
                {narrowed(c) ? <span className="muted"> · {narrowed(c)}</span> : null}
              </td>
              <td className="num">
                {c.expiry} {legs(c)}
              </td>
              <td className="num">{c.units}</td>
              <td className="num" title={`natural ${price(c.perUnit.natural)}`}>
                {price(c.perUnit.credit)}
              </td>
              <td className="num pnl-pos">{fmtCash(times(c.units, c.perUnit.cash))}</td>
              <td className="num">{risk(c, c.units)}</td>
              <td className="num">{fmtMoney(times(c.units, c.perUnit.buyingPower))}</td>
              <td className="num">{fmtCash(times(c.units, c.perUnit.theta))}</td>
              <td className="num">{c.perUnit.bwd === null ? "—" : (c.units * c.perUnit.bwd).toFixed(1)}</td>
              <td className="muted">{c.binding ?? "max contracts"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function pct(v: number | null, digits = 0): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

const BP_CHIP: Record<ManageBpLevel, string> = { ok: "chip chip-ok", over: "chip chip-warn", far_over: "chip chip-bad" };

const BP_WORD: Record<ManageBpLevel, string> = { ok: "under the cap", over: "over the cap", far_over: "far over the cap" };

/** Trades stopped only by how full the book is, sized as if there were room. The chip is where
 *  taking one TODAY would leave buying power against the VIX-scaled cap, graded by the package. */
function WaitingTable({ c }: { c: ManageCandidates }) {
  if (c.waiting.length === 0) return null;
  const warn = c.bpWarnOver === null ? "" : ` (amber within ${pct(c.bpWarnOver)} of it, red past that)`;
  return (
    <>
      <div className="card-subhead">waiting for room: {c.waitingTotal} stopped only by buying power or the theta ceiling</div>
      <p className="muted pf-caption" style={{ marginTop: 0 }}>
        buying power used {pct(c.bpUsedPct)} · cap {pct(c.bpCapPct)} at today's VIX{warn}. Sized as if the book had room;
        the chip is where taking it today would leave you. Close positions to make the room, or take one knowing it runs
        the book past its cap.
      </p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>name</th>
              <th>trade</th>
              <th>legs</th>
              <th title="contracts once there is room (spreads: one of each leg)">qty</th>
              <th title="per-share net price at mid, and the natural fill (sell at the bid, buy at the ask)">price</th>
              <th title="signed cash flow, whole position: a credit received is +">cash</th>
              <th title="a spread's max loss; a short put's loss at the name's 1st-percentile move over the holding period">risk</th>
              <th title="buying power the position ties up">buying power</th>
              <th title="buying power used after taking it today, against the VIX-scaled cap">BP after</th>
              <th title="theta, whole position, dollars a day">θ / day</th>
            </tr>
          </thead>
          <tbody>
            {c.waiting.map((w, i) => {
              const room = w.ifRoom;
              const n = room?.units ?? 0;
              const level = room?.bpLevel ?? null;
              return (
                <tr key={`${w.underlying}-${w.kind}-${i}`}>
                  <td>
                    <Name c={w} />
                  </td>
                  <td>
                    {KIND[w.kind] ?? w.kind}
                    {narrowed(w) ? <span className="muted"> · {narrowed(w)}</span> : null}
                  </td>
                  <td className="num">
                    {w.expiry} {legs(w)}
                  </td>
                  <td className="num">{n}</td>
                  <td className="num" title={`natural ${price(w.perUnit.natural)}`}>
                    {price(w.perUnit.credit)}
                  </td>
                  <td className="num pnl-pos">{fmtCash(times(n, w.perUnit.cash))}</td>
                  <td className="num">{risk(w, n)}</td>
                  <td className="num">{fmtMoney(times(n, w.perUnit.buyingPower))}</td>
                  <td>
                    {level ? (
                      <span className={BP_CHIP[level]} title={BP_WORD[level]}>
                        {pct(room?.bpAfterPct ?? null)}
                      </span>
                    ) : (
                      "—"
                    )}
                    {room?.thetaOver ? (
                      <span
                        className="chip chip-warn"
                        title={`book theta after it ${pct(room.thetaAfterPct, 2)} of net liq, over the ${pct(room.thetaCeilingPct, 1)} ceiling`}
                      >
                        θ over
                      </span>
                    ) : null}
                  </td>
                  <td className="num">{fmtCash(times(n, w.perUnit.theta))}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {c.waitingTotal > c.waiting.length && (
        <p className="muted pf-caption">
          the best {c.waiting.length} of {c.waitingTotal} waiting, by theta per dollar of buying power
        </p>
      )}
    </>
  );
}

function TooBig({ rows, total }: { rows: ManageCandidate[]; total: number }) {
  // A row waiting for room is in the table above; this list keeps what a cap on the trade itself stops.
  const own = rows.filter((r) => r.ifRoom === null);
  if (own.length === 0) return null;
  return (
    <details className="pf-too-big">
      <summary>
        {own.length === rows.length
          ? `${total} that do not fit at even one contract${total > rows.length ? ` (the best ${rows.length} shown)` : ""}`
          : `${own.length} more stopped by a cap on the trade itself${total > rows.length ? " (of the best shown)" : ""}`}
      </summary>
      <ul>
        {own.map((c, i) => (
          <li key={`${c.underlying}-${c.kind}-${i}`}>
            <strong>
              <Name c={c} />
            </strong>{" "}
            {KIND[c.kind] ?? c.kind} {legs(c)} · stopped by <em>{c.binding ?? "—"}</em>
            <span className="muted">
              {" "}
              (one ties up {fmtMoney(c.perUnit.buyingPower)}, risks {risk(c, 1)})
            </span>
            {c.alternative && (
              <span>
                {" "}
                → narrowed to {legs(c.alternative)}:{" "}
                {c.alternative.units > 0 ? `fits ${c.alternative.units}` : `still stopped by ${c.alternative.binding ?? "—"}`}
              </span>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** The checkbox that swaps in manage's ex-earnings list: the same candidates sized as if the earnings
 *  trades' buying power were already free. Shown only where manage wrote one; a refused one is
 *  disabled with manage's reason. */
function ExEarningsToggle({ c, on, set }: { c: ManageCandidates; on: boolean; set: (v: boolean) => void }) {
  const ex = c.exEarnings;
  if (!ex) return null;
  const title = ex.list
    ? `size as if the buying power of ${ex.trades.join(", ")} (${fmtMoney(ex.dollars)}, each one's defined-risk max loss) were free: they close after the call. Their theta and delta still count.`
    : (ex.error ?? "no ex-earnings list");
  return (
    <label className="muted lbl" title={title}>
      <input type="checkbox" checked={on && ex.list !== null} disabled={ex.list === null} onChange={(e) => set(e.target.checked)} />{" "}
      ex earnings buying power
    </label>
  );
}

export function IncomeCard({ c: whole }: { c: ManageCandidates | null }) {
  const [exOn, setExOn] = useState(false);
  if (!whole) return null;
  const ex = whole.exEarnings;
  // Every figure below is one list's: the whole book's, or manage's ex-earnings one.
  const c = exOn && ex?.list ? ex.list : whole;
  const excluded = Object.entries(c.excluded)
    .map(([why, n]) => `${n} ${why}`)
    .join(", ");
  return (
    <Card title="income trades, sized to the account" controls={<ExEarningsToggle c={whole} on={exOn} set={setExOn} />}>
      {c.error ? (
        <p className="muted">{c.error}</p>
      ) : (
        <>
          <p className="muted pf-caption" style={{ marginTop: 0 }}>
            from our own universe ({c.names ?? "—"} tradable names) on the cycle monthly, priced at mid from the {c.sourceSession ?? "—"}{" "}
            afternoon quotes: re-quote before sending · sized for {BASIS[c.accountBasis ?? ""] ?? "an account of unknown kind"}
            {excluded ? ` · left out: ${excluded}` : ""}
            {c !== whole && ex ? ` · ex earnings: ${fmtMoney(ex.dollars)} of buying power treated as free (${ex.trades.join(", ")})` : ""}
          </p>
          {c.fits.length > 0 ? (
            <FitsTable rows={c.fits} />
          ) : (
            <p className="muted">
              {c.waitingTotal > 0
                ? `nothing fits today: ${c.waitingTotal} are waiting for room below`
                : "nothing fits today: every candidate is stopped by a cap below"}
            </p>
          )}
          {c.fitsTotal > c.fits.length && (
            <p className="muted pf-caption">the best {c.fits.length} of {c.fitsTotal} that fit, by theta per dollar of buying power</p>
          )}
          <WaitingTable c={c} />
          <TooBig rows={c.tooBig} total={c.tooBigTotal} />
        </>
      )}
      <p className="muted pf-caption">
        caps: one trade's risk{capPct(c.caps?.perTradeRiskPct)} of net liq · a short put's strike
        {capPct(c.caps?.notionalPerNamePct)} of net liq ({c.accountBasis === "cash" ? "cash" : "margin"}) · buying power under
        the VIX cap · delta in band · theta under its ceiling · no name over{capPct(c.caps?.maxNameThetaShare) || " its share"} of
        the theta the account may carry. Retail heuristics, modelled at mid: nothing here is placed.
      </p>
    </Card>
  );
}
