import type { ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import type { FairValueFields, IncomeList, IncomeListRow } from "@console/shared";
import { SortTh, sortRows, useSort } from "../../components/DataTable";
import { Pager, usePage } from "../../components/ScopeBar";
import { useIncomeList } from "../../lib/api";
import { PremiumSlider, useMinPremium } from "../../components/PremiumSlider";

/**
 * Our own short-put and covered-call lists (2026-10-19): one row a name from the suite's universe,
 * the strike nearest 16 delta, priced at the working price. Every figure is `packages/technicals`
 * `income-lists`'; this formats and filters, and ranks nothing -- rows are by symbol, because the
 * rule has no tested edge and a yield order would read as one. The standing note says so and is
 * never removed while the list shows.
 */

const TITLE: Record<IncomeList, string> = { "short-puts": "Short puts", "covered-calls": "Covered calls" };
const PAGE_SIZES = [50, 100, 200, 500] as const;

const NOTE: Record<IncomeList, string> = {
  "short-puts":
    "In the history study (52 names, 2020–2026, at this working price, after costs) a 16-delta put on " +
    "the 45–60 day monthly earned about what its market exposure earned and close to nothing beyond it; buying it " +
    "back at half the credit did a little better, about +1% a year at the median. It is the least-bad rule tested, " +
    "not an edge, and it has not yet been checked against full option history.",
  "covered-calls":
    "In the history study (16 names, 2020–2026, after costs) writing a call against the shares lost " +
    "to simply holding them on most names, worse at higher deltas and on 45–60 day expiries; a 16-delta call on the " +
    "30–45 day monthly lost least, and SPY was the only name with a consistent gain. It is the least-bad rule tested, " +
    "not an edge, and it has not yet been checked against full option history.",
};

function money(v: number | null): string {
  return v === null ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pct(v: number | null, digits = 1): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

function signedPct(v: number): string {
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}%`;
}

function shortDate(iso: string | null): string {
  if (iso === null || iso === "") return "—";
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

function Flag({ on, onChange, children, title }: { on: boolean; onChange: (on: boolean) => void; children: ReactNode; title: string }) {
  return (
    <div className="mode-toggle" style={{ marginLeft: 0 }}>
      <button type="button" title={title} className={on ? "mode-btn active" : "mode-btn"} onClick={() => onChange(!on)}>
        {children}
      </button>
    </div>
  );
}

/** The next announcement against the trade's own expiry; shared with the credit-spread tab. */
export function EarningsCell({ r }: { r: Pick<IncomeListRow, "earnings" | "earningsDate" | "earningsEstimated"> }) {
  if (r.earnings === "none") return <td className="muted">fund</td>;
  const when = r.earningsDate ? `${shortDate(r.earningsDate)}${r.earningsEstimated ? " (est.)" : ""}` : "";
  if (r.earnings === "in-trade") return <td title="The next announcement falls before this trade's expiry">{`in trade · ${when}`}</td>;
  if (r.earnings === "clear") return <td className="muted" title="The next announcement falls after this trade's expiry">{`after · ${when}`}</td>;
  // A date before the session is the report just made: the broker has not posted the next one yet.
  if (r.earningsDate) {
    return (
      <td className="muted" title="The broker's date is a report already made; the next is not posted yet, so the trade is never called clear">
        {`past · ${shortDate(r.earningsDate)}`}
      </td>
    );
  }
  return <td className="muted" title="No announcement date is known, so the trade is never called clear">unknown</td>;
}

export const FAIR_TITLE =
  "The session's DCF estimate of the share's value (cherrypick.core.valuation on Dolt's annual statements): the price " +
  "against it, with the model's low-high range. A model estimate, tested 2018–26: no edge, never a ranking.";

const FAIR_FLAG: Record<string, string> = {
  terminal_heavy: "the terminal value is over 75% of the enterprise value: the answer is the perpetuity, not the forecast",
  negative_base_fcf: "the first forecast year's cash flow is negative",
  beta_bounded: "the regression beta was outside 0.5-2.0 and was bounded",
  beta_disagrees: "the broker's beta differs from ours by more than 0.5",
  shares_stale: "spot times the last fiscal year's shares is more than 10% off the broker's market cap",
};

const FAIR_STATE: Record<string, string> = {
  "not-applicable": "A fund, a bank or a REIT: an operating-cash-flow DCF does not describe it",
  "no-statements": "No annual statements published by the session",
  refused: "The model refused this name (too little history, no operating profit, a rate too near the growth, or a cash flow that never turns positive)",
  unbuilt: "No fair-value file had been built for the session",
  unlisted: "The fair-value file has no row for this name",
};

/** The fair-value gap: the price against the model's value; shared with the credit-spread tab. */
export function FairCell({ fair }: { fair: FairValueFields | null | undefined }) {
  if (fair === null || fair === undefined) return <td className="muted" title="A list built before the fair-value fields existed">—</td>;
  if (fair.fairState !== "valued" || fair.fairGap === null || fair.fairValue === null) {
    const state = fair.fairState ?? "unbuilt";
    return (
      <td className="muted" title={FAIR_STATE[state] ?? state}>
        {state === "not-applicable" ? "n/a" : "—"}
      </td>
    );
  }
  const flags = fair.fairFlags.map((f) => FAIR_FLAG[f] ?? f);
  const title =
    `Fair value ${money(fair.fairValue)} (range ${money(fair.fairLow)}–${money(fair.fairHigh)}); the price is ` +
    `${signedPct(fair.fairGap)} against it. ${FAIR_TITLE}${flags.length > 0 ? ` Flags: ${flags.join("; ")}.` : ""}`;
  return (
    <td className="num" title={title}>
      {signedPct(fair.fairGap)}
      {fair.fairFlags.length > 0 ? <span className="muted"> †</span> : null}
    </td>
  );
}

function LiquidCell({ r }: { r: IncomeListRow }) {
  if (r.passes === null) return <td className="muted">unquoted</td>;
  const title = `OI ${r.openInterest ?? "—"}, volume ${r.volume ?? "—"}${r.quiet ? " (quiet)" : ""}`;
  return (
    <td title={title} className={r.passes ? undefined : "muted"}>
      {r.passes ? "liquid" : "thin"}
    </td>
  );
}

/** The covered call's ex-dividend date and its state, the way the Earnings cell reads its date. A list
 *  built before the state was recorded carries only the in-trade flag: a flagged row reads in trade,
 *  anything else unknown -- a false there never meant clear. */
function ExDividendCell({ r }: { r: IncomeListRow }) {
  const when = r.exDividendDate ? shortDate(r.exDividendDate) : "";
  const state = r.exDividend ?? (r.exDividendInTrade ? "in-trade" : "unknown");
  if (state === "in-trade") {
    return (
      <td title="An ex-dividend date before this call's expiry: a call in the money then can be assigned early for the dividend">
        {when ? `in trade · ${when}` : "in trade"}
      </td>
    );
  }
  if (state === "after") {
    return (
      <td className="muted" title="The next ex-dividend date falls after this call's expiry">
        {`after · ${when}`}
      </td>
    );
  }
  if (state === "past") {
    return (
      <td className="muted" title="The broker's date is the last ex-dividend date; the next is not posted yet, so the call is never called clear">
        {`past · ${when}`}
      </td>
    );
  }
  return (
    <td className="muted" title="No ex-dividend date is known, so the call is never called clear">
      unknown
    </td>
  );
}

export interface OwnListFilters {
  allTrades: boolean;
  allEarnings: boolean;
  allFit: boolean;
  /** Whole-contract dollars; 0 is off. */
  minPremium: number;
  /** Upper-cased symbol search; "" is off. */
  q: string;
}

/**
 * The rows shown, and why each hidden row is hidden. The counts are layered in the order the filters
 * sit on the page -- liquid trade, earnings, fit, premium, then the symbol search -- so a row failing
 * several is counted once, under the first, and the parts always add up to all less shown.
 */
export function applyFilters(all: IncomeListRow[], f: OwnListFilters) {
  const tradeOk = (r: IncomeListRow) => f.allTrades || r.passes !== false;
  const earningsOk = (r: IncomeListRow) => f.allEarnings || r.earnings !== "in-trade";
  // An unjudged row (no evaluation for the session, or a strike manage did not price) stays shown.
  const fitOk = (r: IncomeListRow) => f.allFit || r.fit !== false;
  // An unquoted row has no premium to compare, so it stays shown, marked, as it does under the others.
  const premiumOk = (r: IncomeListRow) => f.minPremium === 0 || r.premium === null || r.premium >= f.minPremium;
  const matches = (r: IncomeListRow) => f.q === "" || r.symbol.includes(f.q);
  const hidden = { thin: 0, inTrade: 0, unfit: 0, small: 0, unmatched: 0 };
  const shown: IncomeListRow[] = [];
  for (const r of all) {
    if (!tradeOk(r)) hidden.thin += 1;
    else if (!earningsOk(r)) hidden.inTrade += 1;
    else if (!fitOk(r)) hidden.unfit += 1;
    else if (!premiumOk(r)) hidden.small += 1;
    else if (!matches(r)) hidden.unmatched += 1;
    else shown.push(r);
  }
  return { shown, hidden };
}

const SORTS: Record<string, (r: IncomeListRow) => number | string | null> = {
  symbol: (r) => r.symbol,
  price: (r) => r.spot,
  strike: (r) => r.strike,
  delta: (r) => Math.abs(r.delta),
  iv: (r) => r.iv,
  working: (r) => r.working,
  premium: (r) => r.premium,
  ret: (r) => r.returnFraction,
  annual: (r) => r.annualFraction,
  distance: (r) => r.distanceFraction,
  oi: (r) => r.openInterest,
  earnings: (r) => r.earningsDate,
};

export function OwnListTab({ list }: { list: IncomeList }) {
  const [params, setParams] = useSearchParams();
  const dayParam = params.get("day");
  const { data, isLoading, isError } = useIncomeList(list, dayParam);
  const sort = useSort();
  const allTrades = params.get("trade") === "all";
  const allEarnings = params.get("earnings") === "all";
  const allFit = params.get("fit") === "all";
  const q = (params.get("q") ?? "").trim().toUpperCase();
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };

  const all = data?.rows ?? [];
  const minPremium = useMinPremium(list, params.get("premium"));
  const { shown, hidden } = applyFilters(all, { allTrades, allEarnings, allFit, minPremium, q });
  const { thin, inTrade, unfit, small, unmatched } = hidden;
  // Covered calls: manage judges a call unfit only for want of an uncovered 100-share lot, so when
  // every row is unfit the account holds none, and the empty list should say that plainly.
  const noLotHeld = list === "covered-calls" && all.length > 0 && all.every((r) => r.fit === false);
  const sorted = sortRows(shown, sort, SORTS);
  const { page, setOffset, setLimit } = usePage(
    [list, data?.day, allTrades, allEarnings, allFit, minPremium, q, sort.key, sort.dir],
    100,
  );
  const rows = sorted.slice(page.offset, page.offset + page.limit);
  const calls = list === "covered-calls";
  const latest = data?.days[0] ?? null;
  const dropped = Object.entries(data?.dropped ?? {});

  return (
    <section className="card">
      <div className="card-head">
        <h2>{TITLE[list]}</h2>
        <span className="chip">ours</span>
        {data?.day && (
          <span className="card-asof">
            session {data.day}
            {data.chainTakenAt ? ` · quoted ${new Date(data.chainTakenAt).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit" })} ET` : ""}
          </span>
        )}
      </div>

      <p className="muted" style={{ margin: "0 0 10px" }}>
        <strong>No tested edge.</strong> {NOTE[list]}
      </p>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}>
        {data && data.days.length > 0 && (
          <select
            className="chip review-session-select"
            aria-label="Session"
            value={data.day ?? ""}
            onChange={(e) => set("day", e.target.value === latest ? null : e.target.value)}
          >
            {data.days.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        )}
        <Flag
          on={!allTrades}
          onChange={(on) => set("trade", on ? null : "all")}
          title="Hide an option that fails the leg bars in the afternoon reading (10% of mid or a nickel, 100 open). An unquoted one stays shown, marked."
        >
          Liquid trade
        </Flag>
        <Flag
          on={!allEarnings}
          onChange={(on) => set("earnings", on ? null : "all")}
          title="Hide names with an announcement between today and this trade's expiry. An unknown date stays shown, marked unknown."
        >
          No earnings in the trade
        </Flag>
        {data?.fitBasis && (
          <Flag
            on={!allFit}
            onChange={(on) => set("fit", on ? null : "all")}
            title={
              calls
                ? "Hide a call the account holds no uncovered 100-share lot for (shares already written against are spoken for)"
                : `Hide a put whose buying power is more than the ${money(data.fitBasis.availableBp)} the account has (manage's ${data.fitBasis.session} evaluation, ${data.fitBasis.basis === "cash" ? "cash-secured" : "Reg T"})`
            }
          >
            Fits the account
          </Flag>
        )}
        <PremiumSlider
          list={list}
          value={minPremium}
          onChange={(v) => set("premium", v === 0 ? null : String(v))}
          title="Hide a trade whose working premium, for the whole contract, is under this. Steps rather than a straight scale, so the small premiums where most rows sit get room. An unquoted option stays shown. Remembered for next time."
        />
        <input
          className="chip review-session-select"
          value={params.get("q") ?? ""}
          placeholder="symbol"
          aria-label="Filter by symbol"
          onChange={(e) => set("q", e.target.value === "" ? null : e.target.value)}
        />
        <span className="chip" style={{ whiteSpace: "normal" }}>
          {shown.length} of {all.length} names
          {thin > 0 ? ` · ${thin} thin hidden` : ""}
          {inTrade > 0 ? ` · ${inTrade} ${thin > 0 ? "more " : ""}with earnings in the trade hidden` : ""}
          {unfit > 0 ? ` · ${unfit} ${thin + inTrade > 0 ? "more " : ""}that do not fit the account hidden` : ""}
          {small > 0 ? ` · ${small} ${thin + inTrade + unfit > 0 ? "more " : ""}under $${minPremium.toLocaleString()} hidden` : ""}
          {unmatched > 0 ? ` · ${unmatched} not matching “${q}”` : ""}
        </span>
      </div>
      {thin + inTrade + unfit + small > 1 && [thin, inTrade, unfit, small].filter((n) => n > 0).length > 1 && (
        <p className="muted" style={{ margin: "0 0 8px" }}>
          Each hidden name is counted once, under the first filter it fails, in the order the filters sit.
        </p>
      )}
      {noLotHeld && !allFit && (
        <p className="muted" style={{ margin: "0 0 8px" }}>
          The account holds no uncovered 100-share lot, so no call fits — turn off <em>Fits the account</em> to see the list.
        </p>
      )}

      {data?.chainComplete === false && (
        <p className="muted" style={{ margin: "0 0 8px" }}>
          The afternoon reading did not finish{data.chainProblem ? ` (${data.chainProblem})` : ""}, so some names are missing.
        </p>
      )}
      {isError && <p className="muted">Could not read the list.</p>}
      {isLoading && <p className="muted">Reading the list…</p>}
      {data && data.day === null && (
        <p className="muted">
          No list built yet: the afternoon chains are read at 14:45 ET on a trading day (market_report.income) and the list is
          built from them at 15:10.
        </p>
      )}

      {rows.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <SortTh label="Symbol" k="symbol" sort={sort} />
                <SortTh label="Price" k="price" sort={sort} className="num" />
                <th title={calls ? "The covered call's expiry: the monthly 30-45 days out, else the cycle monthly" : "The cycle monthly: 45-60 days out, else 30-60"}>Expiry</th>
                <SortTh label="Strike" k="strike" sort={sort} className="num" />
                <SortTh label="Δ" k="delta" sort={sort} className="num" title="Black-Scholes on the option's own IV, solved from its mid" />
                <SortTh label="IV" k="iv" sort={sort} className="num" />
                <th className="num" title="The afternoon quote, per share">Bid × ask</th>
                <SortTh label="Working" k="working" sort={sort} className="num" title="Per share: the mid less a quarter of the width, what a limit at the mid stepped down fills at" />
                <SortTh label="Premium" k="premium" sort={sort} className="num" title="Whole contract: working × 100" />
                <SortTh
                  label="Return"
                  k="ret"
                  sort={sort}
                  className="num"
                  title={calls ? "Working premium ÷ share price" : "Working premium ÷ strike (the cash it secures)"}
                />
                <SortTh label="Annual" k="annual" sort={sort} className="num" title="Return × 365 ÷ days, simple. Yield, not edge." />
                <SortTh label="Distance" k="distance" sort={sort} className="num" title="Strike against the share price" />
                <th className="num" title={FAIR_TITLE}>
                  Vs fair
                </th>
                <SortTh label="Earnings" k="earnings" sort={sort} title="The next announcement, against this trade's own expiry" />
                {calls && (
                  <th title="The broker's ex-dividend date against this call's expiry: in trade (a call in the money then can be assigned early for the dividend), after, past (the next is not posted) or unknown">
                    Ex-div
                  </th>
                )}
                <th title="The reading's own verdict against the leg bars">Liquid</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol}>
                  <td>
                    {r.symbol}
                    {r.type && r.type !== "Stock" ? <span className="muted"> · {r.type === "Exchange Traded Fund" ? "fund" : r.type}</span> : null}
                    {r.leveraged ? (
                      <span
                        className="chip"
                        title="A daily-reset fund (the declared leveraged, inverse and volatility list): it decays and gaps in ways its delta does not price"
                      >
                        {r.leveraged}
                      </span>
                    ) : null}
                    {r.fit === false ? (
                      <span className="chip muted" title={calls ? "No uncovered 100-share lot held" : "More buying power than the account has"}>
                        does not fit
                      </span>
                    ) : null}
                  </td>
                  <td className="num">{money(r.spot)}</td>
                  <td>
                    {shortDate(r.expiry)} <span className="muted">{r.dte}d</span>
                    {calls && r.window === "cycle" ? <span className="chip" title="No monthly was listed 30-45 days out">cycle</span> : null}
                  </td>
                  <td className="num">
                    {r.strike}
                    {calls ? "C" : "P"}
                  </td>
                  <td className="num">{r.delta.toFixed(2)}</td>
                  <td className="num">{pct(r.iv, 0)}</td>
                  <td className="num">
                    {money(r.bid)} × {money(r.ask)}
                  </td>
                  <td className="num">{money(r.working)}</td>
                  <td className="num">{r.premium === null ? "—" : `$${money(r.premium)}`}</td>
                  <td className="num">{pct(r.returnFraction, 2)}</td>
                  <td className="num">{pct(r.annualFraction)}</td>
                  <td className="num">{signedPct(r.distanceFraction)}</td>
                  <FairCell fair={r.fair} />
                  <EarningsCell r={r} />
                  {calls && <ExDividendCell r={r} />}
                  <LiquidCell r={r} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {shown.length > PAGE_SIZES[0] && (
        <Pager offset={page.offset} limit={page.limit} total={shown.length} pageSizes={PAGE_SIZES} onOffset={setOffset} onLimit={setLimit} />
      )}
      {data && data.day !== null && all.length > 0 && shown.length === 0 && !(noLotHeld && !allFit) && (
        <p className="muted">Nothing matches these filters.</p>
      )}

      <p className="muted" style={{ marginTop: 8 }}>
        The names are our options-tradable universe (weekly options and $100M a day in the stock) and the funds that pass
        the same test. {calls ? "The call nearest 16 delta above the price, on the monthly 30-45 days out (else the cycle monthly), for shares you hold." : "The put nearest 16 delta below the price, on the cycle monthly (45-60 days out, else 30-60)."}{" "}
        Priced from the afternoon reading at the working price. The return is yield, not edge, and the rows are in symbol
        order on purpose.
        {dropped.length > 0 ? ` No row for: ${dropped.map(([k, v]) => `${v} (${k})`).join(", ")}.` : ""}
      </p>
    </section>
  );
}
