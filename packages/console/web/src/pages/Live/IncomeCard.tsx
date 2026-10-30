import { Fragment, useState } from "react";
import type { ManageBpLevel, ManageCandidate, ManageCandidates } from "@console/shared";
import { Card } from "../../components/DataTable";
import { Cells, SecondaryCells, secondaryOf, useColumns, type Column } from "../../components/columns";
import { PremiumSlider, useMinPremium } from "../../components/PremiumSlider";
import { fmtCash, fmtMoney } from "../../lib/format";

/**
 * Income trades from our own universe, sized to the account (packages/manage `candidates.py`): the
 * ones that fit, with their quantity and the cap the next unit would break, and the ones that do not,
 * with the cap that stopped them and the narrowed version where one fits. Every figure is the
 * package's, in its order: by symbol from 2026-10-22, since no structure has a tested edge and a
 * ranking would read as one. Each spread shows its odds (chance of a loss, of the max loss, the
 * break-even, what getting in costs), the reward-against-risk read the studies left the user with.
 * Money follows the suite layout: a credit received is +, whole-position dollars,
 * the one per-share figure is the net price and says so. Priced at mid from the income reading's afternoon
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

/**
 * The per-share net price the cash column is: one unit's cash over the 100 multiplier, to the
 * places it takes (`0.095 cr` beside `+$9.50`). Manage rounds `credit` to the cent but writes `cash`
 * from the unrounded mid, so a half-cent mid once read "0.10 cr" beside "+$9.50". Falls back to the
 * credit where a row carries no cash.
 */
export function netPrice(c: ManageCandidate): string {
  const cash = c.perUnit.cash;
  if (cash === null) return price(c.perUnit.credit);
  const v = cash / 100;
  const three = Math.abs(v).toFixed(3);
  return `${three.endsWith("0") ? Math.abs(v).toFixed(2) : three} ${v >= 0 ? "cr" : "db"}`;
}

function times(n: number, v: number | null): number | null {
  return v === null ? null : n * v;
}

/** The loss the per-trade cap was applied to: a short put's stress loss, a spread's max loss (the
 *  package records it as both). A covered call's risk is the shares already held. */
function risk(c: ManageCandidate, n: number): string {
  return c.kind === "covered_call" ? "covered" : fmtMoney(times(n, c.perUnit.stressLoss));
}

/** A spread's odds in one cell: chance of any loss · of the max loss · break-even · entry cost. */
function Odds({ c }: { c: ManageCandidate }) {
  const o = c.odds;
  if (!o) return <td className="muted">—</td>;
  return (
    <td
      className="num"
      title={`chance of any loss ${pct(o.pLoss)} (its break-even, from the legs' deltas) · of the max loss ${pct(o.pMaxLoss)} (about the long's delta) · break-even ${o.breakEven ?? "—"} · getting in costs ${fmtMoney(o.entryCost)} over the mid (working price and opening fees), ${pct(o.entryCostShare)} of the credit`}
    >
      {pct(o.pLoss)} / {pct(o.pMaxLoss)} · b/e {o.breakEven ?? "—"} · cost {pct(o.entryCostShare)}
    </td>
  );
}

/** What an alternative row is. A spread fitted from a spread is narrower (manage offers one only when
 *  it is, from 2026-10-22); one fitted from a short put is that put with a long bought under it. */
function narrowed(c: ManageCandidate): string | null {
  if (!c.alternativeTo) return null;
  if (c.alternativeTo === c.kind) return "narrowed to fit";
  if (c.alternativeTo === "short_put") return "the short put, with a long bought under it to fit";
  return `fitted from the ${KIND[c.alternativeTo] ?? c.alternativeTo}`;
}

function width(c: ManageCandidate): number | null {
  return c.legs.length === 2 ? Math.abs(c.legs[0]!.strike - c.legs[1]!.strike) : null;
}

/** The verb for a too-big row's alternative, from the two structures' own strikes: an evaluation
 *  written before manage stopped offering them could carry a WIDER spread as "the version that fits". */
function altVerb(c: ManageCandidate, alt: ManageCandidate): string {
  if (c.kind === "short_put") return "as a spread";
  const a = width(alt);
  const b = width(c);
  return a !== null && b !== null && a > b ? "widened to" : "narrowed to";
}

/**
 * The columns both lists share, for a row sized at `n(c)` contracts. Quantity, the per-share price,
 * theta and (on the fits list) delta and the next cap are in the row's expanded line in every view
 * (2026-10-30, docs/console-review-plan.md batch 3: they were the detail view's, the view the console
 * opens in); the per-share price is also on the cash cell's hover. No buying-power dollars column (2026-10-21): what a trade does to
 * the book is the waiting list's `BP after`, and the too-big list states each one's buying power.
 */
function sharedColumns(n: (c: ManageCandidate) => number, qtyTitle: string): Column<ManageCandidate>[] {
  return [
    {
      key: "name",
      label: "name",
      td: (c) => (
        <td>
          <Name c={c} />
        </td>
      ),
    },
    {
      key: "trade",
      label: "trade",
      td: (c) => (
        <td>
          {KIND[c.kind] ?? c.kind}
          {narrowed(c) ? <span className="muted"> · {narrowed(c)}</span> : null}
        </td>
      ),
    },
    {
      key: "legs",
      label: "legs",
      td: (c) => (
        <td className="num">
          {c.expiry} {legs(c)}
        </td>
      ),
    },
    { key: "qty", label: "qty", title: qtyTitle, secondary: true, td: (c) => <td className="num">{n(c)}</td> },
    {
      key: "price",
      label: "price",
      title: "per-share net price at mid, and the natural fill (sell at the bid, buy at the ask)",
      secondary: true,
      td: (c) => (
        <td className="num" title={`natural ${price(c.perUnit.natural)}`}>
          {netPrice(c)}
        </td>
      ),
    },
    {
      key: "cash",
      label: "cash",
      title: "signed cash flow, whole position: a credit received is +",
      td: (c) => (
        <td className="num pnl-pos" title={`${netPrice(c)} a share at mid · ${n(c)} contract${n(c) === 1 ? "" : "s"}`}>
          {fmtCash(times(n(c), c.perUnit.cash))}
        </td>
      ),
    },
    {
      key: "risk",
      label: "risk",
      title: "a spread's max loss; a short put's loss at the name's 1st-percentile move over the holding period",
      td: (c) => <td className="num">{risk(c, n(c))}</td>,
    },
    {
      key: "odds",
      label: "odds",
      title: "a spread's chance of any loss / of the max loss, its break-even, and what getting in costs as a share of the credit",
      td: (c) => <Odds c={c} />,
    },
  ];
}

const THETA_COLUMN = (n: (c: ManageCandidate) => number): Column<ManageCandidate> => ({
  key: "theta",
  label: "θ / day",
  title: "theta, whole position, dollars a day",
  secondary: true,
  td: (c) => <td className="num">{fmtCash(times(n(c), c.perUnit.theta))}</td>,
});

const fitUnits = (c: ManageCandidate) => c.units;

export const FIT_COLUMNS: Column<ManageCandidate>[] = [
  ...sharedColumns(fitUnits, "contracts (spreads: one of each leg)"),
  THETA_COLUMN(fitUnits),
  {
    key: "bwd",
    label: "Δ SPY sh",
    title: "beta-weighted delta the position adds, in SPY shares",
    secondary: true,
    td: (c) => <td className="num">{c.perUnit.bwd === null ? "—" : (c.units * c.perUnit.bwd).toFixed(1)}</td>,
  },
  {
    key: "binding",
    label: "next stops at",
    title: "the cap the next contract would break",
    secondary: true,
    td: (c) => <td className="muted">{c.binding ?? "max contracts"}</td>,
  },
];

const roomUnits = (c: ManageCandidate) => c.ifRoom?.units ?? 0;

const WAITING_COLUMNS: Column<ManageCandidate>[] = [
  ...sharedColumns(roomUnits, "contracts once there is room (spreads: one of each leg)"),
  {
    key: "bpAfter",
    label: "BP after",
    title: "buying power used after taking it today, against the VIX-scaled cap",
    td: (w) => {
      const room = w.ifRoom;
      const level = room?.bpLevel ?? null;
      return (
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
      );
    },
  },
  THETA_COLUMN(roomUnits),
];

/** A list's rows, each opening to its secondary figures; the opener is the row's first cell. */
function CandidateRows({ rows, all }: { rows: ManageCandidate[]; all: Column<ManageCandidate>[] }) {
  const [open, setOpen] = useState<number | null>(null);
  const cols = useColumns(all);
  const secondary = secondaryOf(all);
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th aria-label="more" />
            {cols.map((c) => (
              <th key={c.key} title={c.title}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((c, i) => (
            <Fragment key={`${c.underlying}-${c.kind}-${i}`}>
              <tr>
                <td>
                  <button type="button" className="btn btn-quiet" aria-expanded={open === i} aria-label={`${open === i ? "Hide" : "Show"} ${c.underlying}'s detail`} onClick={() => setOpen(open === i ? null : i)}>
                    {open === i ? "▾" : "▸"}
                  </button>
                </td>
                <Cells cols={cols} row={c} />
              </tr>
              {open === i && (
                <tr>
                  <td colSpan={cols.length + 1}>
                    <SecondaryCells cols={secondary} row={c} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FitsTable({ rows }: { rows: ManageCandidate[] }) {
  return <CandidateRows rows={rows} all={FIT_COLUMNS} />;
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
      <div className="card-subhead">
        waiting for room: {c.waitingTotal} stopped only by buying power, the theta ceiling or the expiry's concentration
      </div>
      <p className="muted pf-caption" style={{ marginTop: 0 }}>
        buying power used {pct(c.bpUsedPct)} · cap {pct(c.bpCapPct)} at today's VIX{warn}. Sized as if the book had room;
        the chip is where taking it today would leave you. Close positions to make the room, or take one knowing it runs
        the book past its cap.
      </p>
      <CandidateRows rows={c.waiting} all={WAITING_COLUMNS} />
      {c.waitingTotal > c.waiting.length && (
        <p className="muted pf-caption">
          the first {c.waiting.length} of {c.waitingTotal} waiting, by symbol
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
          ? `${total} that do not fit at even one contract${total > rows.length ? ` (the first ${rows.length} by symbol shown)` : ""}`
          : `${own.length} more stopped by a cap on the trade itself${total > rows.length ? " (of the first shown by symbol)" : ""}`}
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
                → {altVerb(c, c.alternative)} {legs(c.alternative)}:{" "}
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

/** A candidate at or over the premium floor: one contract's credit, the cash column's per-unit figure.
 *  An unpriced one has nothing to compare, so it stays, as on the Screeners lists. */
const premiumOk = (floor: number) => (r: ManageCandidate) => floor === 0 || r.perUnit.cash === null || r.perUnit.cash >= floor;

export function IncomeCard({ c: whole }: { c: ManageCandidates | null }) {
  const [exOn, setExOn] = useState(false);
  const minPremium = useMinPremium("live-income");
  if (!whole) return null;
  const ex = whole.exEarnings;
  // Every figure below is one list's: the whole book's, or manage's ex-earnings one, cut to the floor.
  // The totals are manage's and count past the rows it wrote, so a cut list states only what it shows.
  const listed = exOn && ex?.list ? ex.list : whole;
  const ok = premiumOk(minPremium);
  const cut = (rows: ManageCandidate[]) => rows.filter(ok);
  const c: ManageCandidates =
    minPremium === 0
      ? listed
      : {
          ...listed,
          fits: cut(listed.fits),
          fitsTotal: cut(listed.fits).length,
          waiting: cut(listed.waiting),
          waitingTotal: cut(listed.waiting).length,
          tooBig: cut(listed.tooBig),
          tooBigTotal: cut(listed.tooBig).length,
        };
  const hidden = listed.fits.length + listed.waiting.length + listed.tooBig.length - c.fits.length - c.waiting.length - c.tooBig.length;
  const excluded = Object.entries(c.excluded)
    .map(([why, n]) => `${n} ${why}`)
    .join(", ");
  return (
    <Card
      title="income trades, sized to the account"
      controls={
        <>
          <PremiumSlider
            list="live-income"
            value={minPremium}
            title="Hide a trade whose credit for one contract is under this. An unpriced one stays shown. Remembered for next time."
          />
          {hidden > 0 && <span className="chip">{hidden} under ${minPremium.toLocaleString()} hidden</span>}
          <ExEarningsToggle c={whole} on={exOn} set={setExOn} />
        </>
      }
    >
      {c.error ? (
        <p className="muted">{c.error}</p>
      ) : (
        <>
          <p className="muted pf-caption" style={{ marginTop: 0 }}>
            from our own universe ({c.names ?? "—"} tradable names) on the cycle monthly, priced at mid from the {c.sourceSession ?? "—"}{" "}
            afternoon quotes: re-quote before sending · sized for {BASIS[c.accountBasis ?? ""] ?? "an account of unknown kind"}
            {excluded ? ` · left out: ${excluded}` : ""}
            {listed !== whole && ex ? ` · ex earnings: ${fmtMoney(ex.dollars)} of buying power treated as free (${ex.trades.join(", ")})` : ""}
          </p>
          <p className="muted pf-caption" style={{ marginTop: 0 }}>
            <strong>No tested edge.</strong> The 2026-10 credit-spread studies found no short strike, direction signal or exit
            that earns after costs; a put spread short near 0.20 delta, long near 0.10, held to expiry was the least bad, and
            closing at half the credit did worse than holding. Listed by symbol, never ranked. Call spreads are off: short
            delta comes from the hedge when the book needs it.
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
            <p className="muted pf-caption">the first {c.fits.length} of {c.fitsTotal} that fit, by symbol</p>
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
