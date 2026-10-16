import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { ScreenerLeg, ScreenerList, ScreenerRate, ScreenerRow } from "@console/shared";
import { SortTh, sortRows, useSort } from "../../components/DataTable";
import { Pager, usePage } from "../../components/ScopeBar";
import { useScreener, useScreenerFidelity } from "../../lib/api";

/**
 * The income screener lists (2026-10-16): short puts, covered calls and credit spreads, one row a
 * name, as the evening capture saved them. Read-only, and it decides nothing: every figure is the
 * list's own, `packages/technicals`' rule (`score-screeners`), `fetch_screener_greeks.py`'s liquidity
 * readings, or our own tradable verdict and earnings dates, joined by the server.
 *
 * Two layers, as the plan doc settles them. The LISTED trade is the screen's own expiry and strike
 * (often a weekly), with our rule's strike beside it; that is the reproduction. The TRADE column is
 * the user's: the listed option where it is liquid (10% of mid or a nickel, 100 open, 10 traded),
 * otherwise the same strike on the standard monthly 45-60 days out (else 30-60), with a "thin" mark
 * when that monthly fails the same bars. Earnings are judged against that cycle monthly, not the
 * listed expiry, and a date that does not reach it is "unknown", never clear.
 *
 * Defaults cut to what the user would trade: options-tradable names, no earnings inside the cycle.
 * Each has its escape. Which names make each list is NOT reproduced; the page says so.
 */

const LIST_TITLE: Record<ScreenerList, string> = {
  "short-puts": "Short puts",
  "covered-calls": "Covered calls",
  "credit-spreads": "Credit spreads",
};
const PAGE_SIZES = [50, 100, 200, 500] as const;

function money(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function strike(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : String(v);
}

function pct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(digits)}%`;
}

function signedPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}%`;
}

/** "2026-11-13" -> "Nov 13". */
function shortDate(iso: string | null): string {
  if (iso === null) return "—";
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
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

function legLine(l: ScreenerLeg): string {
  return (
    `${l.role} ${l.strike}${l.right} ${l.expiry}: ${money(l.bid)} × ${money(l.ask)}, ` +
    `OI ${l.openInterest ?? "—"}, volume ${l.volume ?? "—"}` +
    (l.passes === null ? "" : l.passes ? " — passes" : " — fails")
  );
}

/** Our rule's strike(s): a tick when they are the list's own, else ours; a dash when not judged. */
function OursCell({ r }: { r: ScreenerRow }) {
  if (r.ours === null) return <td className="muted" title="This session's broker chain was not recorded for this name">—</td>;
  const same = r.ours.length === r.strikes.length && r.ours.every((k, i) => k === r.strikes[i]);
  return same ? (
    <td className="pnl-pos" title="Our reproduced rule picks the same strike(s)">✓</td>
  ) : (
    <td className="num" title="Our reproduced rule's strike(s) where they differ from the list's">
      {r.ours.map(strike).join(" / ")}
    </td>
  );
}

function EarningsCell({ r }: { r: ScreenerRow }) {
  if (r.earnings === "none") {
    return (
      <td className="muted" title="A fund: no earnings announcements">
        fund
      </td>
    );
  }
  const cls = r.earnings === "in-cycle" ? "chip chip-warn" : r.earnings === "clear" ? "chip chip-ok" : "chip";
  const label = r.earnings === "in-cycle" ? "in cycle" : r.earnings;
  const title =
    `Next announcement ${r.earningsDate ?? "unknown"}` +
    (r.earningsSource ? ` (${r.earningsSource === "broker" ? "broker" : "the list"}${r.earningsEstimated ? ", estimated" : ""})` : "") +
    `; cycle monthly ${r.cycleExpiry ?? "none listed"}. A date that does not reach the cycle expiry is unknown, never clear.`;
  return (
    <td title={title}>
      {r.earningsDate ? shortDate(r.earningsDate) : ""} <span className={cls}>{label}</span>
    </td>
  );
}

/** The user's trade: the listed option when liquid, else the cycle monthly (marked thin if it fails too). */
function TradeCell({ r }: { r: ScreenerRow }) {
  if (r.recommendation === null) {
    return (
      <td className="muted" title="No regular-hours liquidity reading of this list yet: they are taken at 11:30 and 14:30 ET the next session, and names held illiquid are skipped">
        no reading
      </td>
    );
  }
  const legs = [...r.listedLegs, ...(r.cycleLegs ?? [])].map(legLine).join("\n");
  if (r.recommendation === "listed") {
    return (
      <td className="pnl-pos" title={`The listed option is liquid enough to trade.\n${legs}`}>
        listed
      </td>
    );
  }
  if (r.recommendation === "none" || r.cycleLegs === null || r.cycleLegs.length === 0) {
    return (
      <td className="muted" title={`The listed option is thin and no cycle monthly is listed.\n${legs}`}>
        none
      </td>
    );
  }
  const strikes = r.cycleLegs.map((l) => `${l.strike}${l.right}`).join("/");
  return (
    <td title={`The listed option is thin: the same strike on the cycle monthly instead.\n${legs}`}>
      {shortDate(r.cycleLegs[0]!.expiry)} {strikes}
      {r.cyclePasses === false && (
        <>
          {" "}
          <span className="chip chip-warn" title="The cycle monthly fails the same liquidity bars">
            thin
          </span>
        </>
      )}
    </td>
  );
}

function ExpiryCell({ r }: { r: ScreenerRow }) {
  return (
    <td title={`${r.expiry}, ${r.dte ?? "—"} days, ${r.weekly ? "a weekly" : "the monthly"}`}>
      {shortDate(r.expiry)} <span className="muted">{r.dte ?? "—"}d{r.weekly ? " · wk" : ""}</span>
    </td>
  );
}

function SymbolCell({ r }: { r: ScreenerRow }) {
  return (
    <td>
      <Link className="module-link" to={`/charts/technicals?symbol=${encodeURIComponent(r.symbol)}`} title={`Open ${r.symbol}'s chart`}>
        {r.symbol}
      </Link>
      {r.liquid === false && (
        <span className="muted" title="Not options-tradable by our nightly verdict (weekly options and $100M a day)">
          {" "}
          · no opts
        </span>
      )}
    </td>
  );
}

const SORTS: Record<string, (r: ScreenerRow) => number | string | null> = {
  symbol: (r) => r.symbol,
  price: (r) => r.price,
  ivr: (r) => r.ivRank,
  expiry: (r) => r.dte,
  strike: (r) => r.strikes[0] ?? null,
  mid: (r) => r.mid,
  raw: (r) => r.rawReturnPct,
  ann: (r) => r.annualisedPct,
  dist: (r) => r.distancePct,
  width: (r) => r.width,
  creditPct: (r) => r.creditPct,
  earnings: (r) => r.earningsDate,
};

export function ScreenerTab({ list }: { list: ScreenerList }) {
  const [params, setParams] = useSearchParams();
  const dayParam = params.get("day");
  const { data, isLoading, isError } = useScreener(list, dayParam);
  const sort = useSort();
  const allNames = params.get("names") === "all";
  const allEarnings = params.get("earnings") === "all";
  const q = (params.get("q") ?? "").trim().toUpperCase();
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };

  const all = data?.rows ?? [];
  const shown = all.filter(
    (r) => (allNames || r.liquid !== false) && (allEarnings || r.earnings !== "in-cycle") && (q === "" || r.symbol.includes(q)),
  );
  const heldOut = all.filter((r) => r.liquid === false).length;
  const inCycle = all.filter((r) => r.earnings === "in-cycle" && (allNames || r.liquid !== false)).length;
  const sorted = sortRows(shown, sort, SORTS);
  const { page, setOffset, setLimit } = usePage([list, data?.day, allNames, allEarnings, q, sort.key, sort.dir], 100);
  const rows = sorted.slice(page.offset, page.offset + page.limit);
  const spread = list === "credit-spreads";
  const latest = data?.days[0] ?? null;

  return (
    <section className="card">
      <div className="card-head">
        <h2>{LIST_TITLE[list]}</h2>
        <span className="chip">read-only</span>
        {data?.day && (
          <span className="card-asof">
            list of {data.day}
            {data.pricesAt ? ` · priced ${data.pricesAt}` : data.createdAt ? ` · built ${data.createdAt}` : ""}
          </span>
        )}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}>
        {data && data.days.length > 0 && (
          <select
            className="chip review-session-select"
            aria-label="List day"
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
          on={!allNames}
          onChange={(on) => set("names", on ? null : "all")}
          title="Only names our nightly verdict calls options-tradable: weekly options listed, and $100M a day in the stock. Off shows every listed name."
        >
          Options-tradable
        </Flag>
        <Flag
          on={!allEarnings}
          onChange={(on) => set("earnings", on ? null : "all")}
          title="Hide names with an announcement between today and the cycle monthly (45-60 days out, else 30-60). An unknown date stays shown, marked unknown."
        >
          No earnings in cycle
        </Flag>
        <input
          className="chip review-session-select"
          value={params.get("q") ?? ""}
          placeholder="symbol"
          aria-label="Filter by symbol"
          onChange={(e) => set("q", e.target.value === "" ? null : e.target.value)}
        />
        <span className="chip" style={{ whiteSpace: "normal" }}>
          {shown.length} of {all.length} names
          {!allNames && heldOut > 0 ? ` · ${heldOut} not tradable hidden` : ""}
          {!allEarnings && inCycle > 0 ? ` · ${inCycle} with earnings in cycle hidden` : ""}
        </span>
      </div>

      {data && data.day !== null && !data.scored && (
        <p className="muted" style={{ margin: "0 0 8px" }}>
          Our rule has not scored this list yet: its broker chain is recorded the same evening (19:00, again at 23:30 ET) and
          scored the next morning, so the “ours” column fills in then.
        </p>
      )}
      {isError && <p className="muted">Could not read the screener lists.</p>}
      {isLoading && <p className="muted">Reading the lists…</p>}
      {data && data.day === null && <p className="muted">No list saved yet; the evening chart capture saves them.</p>}

      {rows.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <SortTh label="Symbol" k="symbol" sort={sort} />
                {spread && <th>Direction</th>}
                <SortTh label="Price" k="price" sort={sort} className="num" />
                <SortTh label="IVR" k="ivr" sort={sort} className="num" title="The list's own IV rank, 0-100" />
                <SortTh label="Expiry" k="expiry" sort={sort} title="The listed expiry, its days out, and whether it is a weekly" />
                <SortTh label={spread ? "Sell / buy" : "Strike"} k="strike" sort={sort} className="num" />
                <th title="The strike(s) our reproduced rule picks: a tick when they are the list's own">Ours</th>
                <SortTh label={spread ? "Credit" : "Mid"} k="mid" sort={sort} className="num" title="Per share" />
                {spread ? (
                  <>
                    <SortTh label="Width" k="width" sort={sort} className="num" />
                    <SortTh label="Credit %" k="creditPct" sort={sort} className="num" title="Credit as a share of the width; the list's floor is a third" />
                  </>
                ) : (
                  <>
                    <SortTh label="Raw" k="raw" sort={sort} className="num" title="Mid ÷ share price" />
                    <SortTh label="Annual" k="ann" sort={sort} className="num" title="Raw × 365 ÷ days, simple" />
                    <SortTh label="Distance" k="dist" sort={sort} className="num" title="Strike against the share price" />
                  </>
                )}
                <SortTh label="Earnings" k="earnings" sort={sort} title="The next announcement, judged against the cycle monthly" />
                <th title="The listed option when it is liquid, else the same strike on the cycle monthly; 'thin' when that monthly fails the same bars">
                  Trade
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol}>
                  <SymbolCell r={r} />
                  {spread && <td className={r.direction === "bullish" ? "pnl-pos" : "pnl-neg"}>{r.direction === "bullish" ? "bull put" : "bear call"}</td>}
                  <td className="num">{money(r.price)}</td>
                  <td className="num">{r.ivRank === null ? "—" : Math.round(r.ivRank)}</td>
                  <ExpiryCell r={r} />
                  <td className="num">
                    {r.strikes.map(strike).join(" / ")}
                    {r.right === "P" ? "P" : "C"}
                  </td>
                  <OursCell r={r} />
                  <td className="num">{money(r.mid)}</td>
                  {spread ? (
                    <>
                      <td className="num">{money(r.width)}</td>
                      <td className="num">{pct(r.creditPct)}</td>
                    </>
                  ) : (
                    <>
                      <td className="num">{pct(r.rawReturnPct, 2)}</td>
                      <td className="num">{pct(r.annualisedPct)}</td>
                      <td className="num">{signedPct(r.distancePct)}</td>
                    </>
                  )}
                  <EarningsCell r={r} />
                  <TradeCell r={r} />
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
        The listed trade is the screen's own expiry and strike; “Ours” is the strike our reproduction of its rule picks
        {spread
          ? " (short: the first strike out of the money; long: nearest 0.25 delta)"
          : list === "short-puts"
            ? " (the put nearest 0.40 delta on the expiry nearest 30 days)"
            : " (the call nearest 0.165 delta on the expiry nearest 45 days)"}
        . Which names make the list is not reproduced. “Trade” is yours: the listed option when it is liquid in regular
        hours, else the same strike on the standard monthly you trade. Prices are the list's, after the close; mids are not
        fills.
      </p>
    </section>
  );
}

function rate(r: ScreenerRate): string {
  // Under 20 rows a percentage overstates itself: one row of one is not "0%" of anything.
  if (r.n === 0) return "—";
  return r.n < 20 ? `${r.hit}/${r.n}` : `${((r.hit / r.n) * 100).toFixed(0)}%`;
}

function RateCell({ r }: { r: ScreenerRate }) {
  return (
    <td className="num" title={`${r.hit} of ${r.n}`}>
      {rate(r)}
    </td>
  );
}

/** How well our reproduced rules match the lists, session by session. A series: never sorted. */
export function FidelityTab() {
  const { data, isLoading, isError } = useScreenerFidelity();
  const rows = data?.sessions ?? [];
  return (
    <section className="card">
      <div className="card-head">
        <h2>Reproduction fidelity</h2>
        <span className="chip">record-only</span>
      </div>
      {isError && <p className="muted">Could not read the scores.</p>}
      {isLoading && <p className="muted">Reading the scores…</p>}
      {data && rows.length === 0 && <p className="muted">No session scored yet: `score-screeners` runs at 06:40 ET.</p>}
      {rows.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th rowSpan={2}>Session</th>
                <th colSpan={3}>Short puts</th>
                <th colSpan={3}>Covered calls</th>
                <th colSpan={4}>Credit spreads</th>
              </tr>
              <tr>
                <th className="num" title="Strike, monthly rows">monthly</th>
                <th className="num" title="Strike, weekly rows">weekly</th>
                <th className="num" title="Expiry, where a chain listing was recorded">expiry</th>
                <th className="num">monthly</th>
                <th className="num">weekly</th>
                <th className="num">expiry</th>
                <th className="num" title="Short leg: the first strike out of the money">short</th>
                <th className="num" title="Long leg: nearest 0.25 delta">long</th>
                <th className="num">expiry</th>
                <th className="num" title="Credit at least a third of the width">floor</th>
              </tr>
            </thead>
            <tbody>
              {data?.totals && (
                <tr>
                  <td>
                    <strong>all {rows.length}</strong>
                  </td>
                  <RateCell r={data.totals.shortPuts.monthly} />
                  <RateCell r={data.totals.shortPuts.weekly} />
                  <RateCell r={data.totals.shortPuts.expiry} />
                  <RateCell r={data.totals.coveredCalls.monthly} />
                  <RateCell r={data.totals.coveredCalls.weekly} />
                  <RateCell r={data.totals.coveredCalls.expiry} />
                  <RateCell r={data.totals.creditSpreads.short} />
                  <RateCell r={data.totals.creditSpreads.long} />
                  <RateCell r={data.totals.creditSpreads.expiry} />
                  <RateCell r={data.totals.creditSpreads.floor} />
                </tr>
              )}
              {rows.map((s) => (
                <tr key={s.day}>
                  <td>{s.day}</td>
                  <RateCell r={s.shortPuts.monthly} />
                  <RateCell r={s.shortPuts.weekly} />
                  <RateCell r={s.shortPuts.expiry} />
                  <RateCell r={s.coveredCalls.monthly} />
                  <RateCell r={s.coveredCalls.weekly} />
                  <RateCell r={s.coveredCalls.expiry} />
                  <RateCell r={s.creditSpreads.short} />
                  <RateCell r={s.creditSpreads.long} />
                  <RateCell r={s.creditSpreads.expiry} />
                  <RateCell r={s.creditSpreads.floor} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted" style={{ marginTop: 8 }}>
        Each session's lists against our reproduced rules, scored from the broker chain recorded that evening. Strikes use
        Black-Scholes delta on each strike's own broker IV. A miss is almost always one strike either side: the list is built
        mid-afternoon and our chain is taken at 19:00 ET. Which names make each list is not reproduced.
      </p>
    </section>
  );
}
