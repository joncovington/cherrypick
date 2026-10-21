import type { ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import type { IncomeList, IncomeListRow } from "@console/shared";
import { SortTh, sortRows, useSort } from "../../components/DataTable";
import { Pager, usePage } from "../../components/ScopeBar";
import { useIncomeList } from "../../lib/api";

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

/** The premium slider's stops, whole-contract dollars. 0 is off. Stepped, because most rows' premiums
 *  are tens of dollars and a straight scale to the largest would leave them a few pixels. */
const PREMIUM_STEPS: readonly number[] = [0, 10, 25, 50, 75, 100, 150, 200, 300, 500, 750, 1000];

/** `?premium=` read back onto a stop: anything else (a hand-typed 60, junk) is the nearest stop below. */
function premiumFloor(raw: string | null): number {
  const v = Number(raw);
  if (!raw || !Number.isFinite(v) || v <= 0) return 0;
  return [...PREMIUM_STEPS].reverse().find((s) => s <= v) ?? 0;
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

function EarningsCell({ r }: { r: IncomeListRow }) {
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

function LiquidCell({ r }: { r: IncomeListRow }) {
  if (r.passes === null) return <td className="muted">unquoted</td>;
  const title = `OI ${r.openInterest ?? "—"}, volume ${r.volume ?? "—"}${r.quiet ? " (quiet)" : ""}`;
  return (
    <td title={title} className={r.passes ? undefined : "muted"}>
      {r.passes ? "liquid" : "thin"}
    </td>
  );
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
  const tradeOk = (r: IncomeListRow) => allTrades || r.passes !== false;
  const earningsOk = (r: IncomeListRow) => allEarnings || r.earnings !== "in-trade";
  // An unjudged row (no evaluation for the session, or a strike manage did not price) stays shown.
  const fitOk = (r: IncomeListRow) => allFit || r.fit !== false;
  const minPremium = premiumFloor(params.get("premium"));
  // An unquoted row has no premium to compare, so it stays shown, marked, as it does under the others.
  const premiumOk = (r: IncomeListRow) => minPremium === 0 || r.premium === null || r.premium >= minPremium;
  const shown = all.filter(
    (r) => tradeOk(r) && earningsOk(r) && fitOk(r) && premiumOk(r) && (q === "" || r.symbol.includes(q)),
  );
  const thin = all.filter((r) => r.passes === false && earningsOk(r) && fitOk(r) && premiumOk(r)).length;
  const inTrade = all.filter((r) => r.earnings === "in-trade" && tradeOk(r) && fitOk(r) && premiumOk(r)).length;
  const unfit = all.filter((r) => r.fit === false && tradeOk(r) && earningsOk(r) && premiumOk(r)).length;
  const small = all.filter((r) => !premiumOk(r) && tradeOk(r) && earningsOk(r) && fitOk(r)).length;
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
        <label
          className="chip"
          style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
          title="Hide a trade whose working premium, for the whole contract, is under this. Steps rather than a straight scale, so the small premiums where most rows sit get room. An unquoted option stays shown."
        >
          Premium ≥ {minPremium === 0 ? "any" : `$${minPremium.toLocaleString()}`}
          <input
            type="range"
            aria-label="Minimum premium per contract"
            min={0}
            max={PREMIUM_STEPS.length - 1}
            step={1}
            value={PREMIUM_STEPS.indexOf(minPremium)}
            onChange={(e) => {
              const v = PREMIUM_STEPS[Number(e.target.value)] ?? 0;
              set("premium", v === 0 ? null : String(v));
            }}
          />
        </label>
        <input
          className="chip review-session-select"
          value={params.get("q") ?? ""}
          placeholder="symbol"
          aria-label="Filter by symbol"
          onChange={(e) => set("q", e.target.value === "" ? null : e.target.value)}
        />
        <span className="chip" style={{ whiteSpace: "normal" }}>
          {shown.length} of {all.length} names
          {!allTrades && thin > 0 ? ` · ${thin} thin hidden` : ""}
          {!allEarnings && inTrade > 0 ? ` · ${inTrade} with earnings in the trade hidden` : ""}
          {!allFit && unfit > 0 ? ` · ${unfit} that do not fit the account hidden` : ""}
          {small > 0 ? ` · ${small} under $${minPremium.toLocaleString()} hidden` : ""}
        </span>
      </div>

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
                <SortTh label="Earnings" k="earnings" sort={sort} title="The next announcement, against this trade's own expiry" />
                {calls && <th title="An ex-dividend date before expiry: a call in the money then can be assigned early for the dividend">Ex-div</th>}
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
                  <EarningsCell r={r} />
                  {calls && <td>{r.exDividendInTrade ? "in trade" : <span className="muted">—</span>}</td>}
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
      {data && data.day !== null && all.length > 0 && shown.length === 0 && <p className="muted">Nothing matches these filters.</p>}

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
