import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { ScreenerLeg, ScreenerList, ScreenerRow } from "@console/shared";
import { SortTh, sortRows, useSort } from "../../components/DataTable";
import { Pager, usePage } from "../../components/ScopeBar";
import { useScreener } from "../../lib/api";

/**
 * The income screener lists (2026-10-16): short puts, covered calls and credit spreads, one row a
 * name, as the evening capture saved them. Read-only, and it decides nothing: every figure is the
 * list's own, `packages/technicals`' rule (`score-screeners`), `fetch_screener_greeks.py`'s liquidity
 * readings, or our own tradable verdict and earnings dates, joined by the server.
 *
 * Two layers, as the plan doc settles them. The LISTED trade is the screen's own expiry and strike
 * (often a weekly), with our rule's strike beside it; that is the reproduction. The TRADE column is
 * the user's: the listed option where it is liquid (10% of mid or a nickel and 100 open; a spread
 * judged as one order, on what it costs to trade against its credit), otherwise the same strike on
 * the standard monthly 45-60 days out (else 30-60), with a "thin" mark when that monthly fails the
 * same bars. Earnings are judged against the expiry of that trade (the later of the two until a
 * reading says which), and a date that does not reach it is "unknown", never clear.
 *
 * The trade is priced from the regular-hours reading at the WORKING price -- halfway from the mid to
 * the natural fill -- and leads the return columns (2026-10-19): the list's own return is at the mid
 * of an evening quote, often a weekly with no bid, and a row whose quote cannot back it says so
 * instead of showing a yield.
 *
 * Defaults cut to what the user would trade: options-tradable names, no earnings inside the cycle,
 * and a liquid trade (the listed option or the cycle monthly passes the bars). Each has its escape. Which names make each list is NOT reproduced; the page says so.
 */

const LIST_TITLE: Record<ScreenerList, string> = {
  "short-puts": "Short puts",
  "covered-calls": "Covered calls",
  "credit-spreads": "Credit spreads",
};
const PAGE_SIZES = [50, 100, 200, 500] as const;

/** The last day the vendor's lists are captured (the subscription ends). */
const VENDOR_END = "2026-10-24";

/** What our studies found for each list's kind of trade (docs/income-review-plan.md). */
const VENDOR_NOTE: Record<ScreenerList, string> = {
  "short-puts":
    "This is the vendor's screen, not a rule of ours. Our short-put studies (0.40-delta puts like this list's, and 16 delta on " +
    "the 45-60 day monthly, after costs) found no edge beyond the market exposure the put carries.",
  "covered-calls":
    "This is the vendor's screen, not a rule of ours. Our covered-call study (16 names, 2020–2026, after costs) found writing " +
    "the call lost to holding the shares on most names.",
  "credit-spreads":
    "This is the vendor's screen, not a rule of ours. Our credit-spread studies found none with edge after costs: no short " +
    "delta (0.20, 0.25, 0.30 or the first out of the money) on either side, no direction signal (the one that passed history, " +
    "on single names), and no put spread on the funds traded; the declared selection test gave no verdict.",
};

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
    `OI ${l.openInterest ?? "—"}, volume ${l.volume ?? "—"}${l.quiet ? " (quiet)" : ""}` +
    (l.passes === null ? "" : l.passes ? " — passes" : " — fails on its own")
  );
}

/** The list's own return, unless its quote cannot back it: no bid, or a spread wider than the mid. */
function ListedReturnCell({ r, v, digits = 1 }: { r: ScreenerRow; v: number | null; digits?: number }) {
  if (r.listedQuote !== null) {
    const why =
      r.listedQuote === "no-bid"
        ? `The list's quote has no bid (${money(r.bid)} × ${money(r.ask)}): its return is priced at a mid nobody would pay.`
        : `The list's quote is wider than its own mid (${money(r.bid)} × ${money(r.ask)}): its mid-priced return is not a fill.`;
    return (
      <td className="num muted" title={`${why} The list states ${pct(v, digits)}.`}>
        <span className="chip chip-warn">{r.listedQuote === "no-bid" ? "no bid" : "wide"}</span>
      </td>
    );
  }
  return <td className="num">{pct(v, digits)}</td>;
}

function tradeTitle(r: ScreenerRow): string {
  const t = r.trade!;
  const where = t.side === "listed" ? "the listed option" : "the cycle monthly";
  // A spread's prices are signed cash flow: below zero it costs to put on, a debit.
  const px = (v: number) => (r.width === null ? money(v) : `${money(Math.abs(v))} ${v < 0 ? "db" : "cr"}`);
  return (
    `${where}, ${t.expiry} (${t.dte ?? "—"} days), from the regular-hours reading: ` +
    `mid ${px(t.mid)}, working ${px(t.working)}, natural ${px(t.natural)} ` +
    `(${money(t.cost)} a share to cross the whole spread). Working is halfway from the mid to the natural fill: ` +
    `a limit at the mid stepped down a quarter of the spread. A model of a fill, not one.`
  );
}

/** The list's floor: a credit of a third of the width. Exact, so 33.12% is not under it. */
const CREDIT_FLOOR_PCT = 100 / 3;

/** Per share at the working price. A spread's price is signed cash flow: a credit `cr`, and a working
 *  price below zero -- the spread costs to put on -- a debit, `db`. */
function TradePriceCell({ r }: { r: ScreenerRow }) {
  if (r.trade === null) return <td className="num muted">—</td>;
  const w = r.trade.working;
  const spread = r.width !== null;
  return (
    <td className={spread && w < 0 ? "num pnl-neg" : "num"} title={tradeTitle(r)}>
      {spread ? `${money(Math.abs(w))} ${w < 0 ? "db" : "cr"}` : money(w)}
      {r.trade.shortInTheMoney ? (
        <>
          {" "}
          <span
            className="chip chip-warn"
            title={`The short ${r.trade.spot === null ? "" : `is in the money at ${money(r.trade.spot)}, `}the share price at the afternoon reading this trade is priced from; the list's own price (${money(r.price)}) is the evening before. The trade's figures are priced on that day's chain.`}
          >
            ITM
          </span>
        </>
      ) : null}
    </td>
  );
}

/** The trade's return at the working price: annualised for one option, credit share for a spread. */
function TradeReturnCell({ r }: { r: ScreenerRow }) {
  const t = r.trade;
  if (t === null) return <td className="num muted">—</td>;
  if (t.workingCreditPct !== null) {
    const under = t.workingCreditPct < CREDIT_FLOOR_PCT;
    return (
      <td className={under ? "num pnl-neg" : "num"} title={`${tradeTitle(r)}\nThe list's floor is a third of the width; tested here at the working price.`}>
        {pct(t.workingCreditPct)}
      </td>
    );
  }
  return (
    <td className="num" title={`${tradeTitle(r)}\nAt the natural fill: ${pct(t.naturalAnnualisedPct)}.`}>
      {pct(t.workingAnnualisedPct)}
    </td>
  );
}

/** Our rule's strike(s): a tick when they are the list's own, else ours; a dash when not judged. */
function OursCell({ r }: { r: ScreenerRow }) {
  if (r.ours === null) return <td className="muted" title="This session's broker chain was not recorded for this name">—</td>;
  const same = r.ours.length === r.strikes.length && r.ours.every((k, i) => k === r.strikes[i]);
  const unpicked = r.ours.some((k) => k === null);
  return same ? (
    <td className="pnl-pos" title="Our reproduced rule picks the same strike(s)">✓</td>
  ) : (
    <td
      className="num"
      title={
        unpicked
          ? "Our reproduced rule's strike(s) where they differ from the list's. “no pick”: the recorded broker chain had no priced strike past the short on that side, so the rule could not choose a long"
          : "Our reproduced rule's strike(s) where they differ from the list's"
      }
    >
      {r.ours.map((k) => (k === null ? "no pick" : strike(k))).join(" / ")}
    </td>
  );
}

function earningsBasis(r: ScreenerRow): string {
  if (r.recommendation === "listed") return "the listed expiry, the trade";
  if (r.recommendation === "cycle") return "the cycle monthly, the trade";
  return "the later of the listed expiry and the cycle monthly, until a liquidity reading says which is the trade";
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
    `; judged from ${r.earningsFrom} (the day of the reading the trade is priced from; a report before the open that day is already behind it) to ${r.earningsExpiry ?? "no expiry"} (${earningsBasis(r)}). A date that does not reach that expiry is unknown, never clear.`;
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

/** No liquid trade: the listed option is thin and the cycle monthly fails the same bars (or none is
 *  listed). A row with no reading yet is not thin -- it is unread. */
function isThin(r: ScreenerRow): boolean {
  return r.recommendation === "none" || (r.recommendation === "cycle" && r.cyclePasses === false);
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
  creditPct: (r) => r.creditPct,
  earnings: (r) => r.earningsDate,
  tradePrice: (r) => r.trade?.working ?? null,
  tradeReturn: (r) => r.trade?.workingCreditPct ?? r.trade?.workingAnnualisedPct ?? null,
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

  const allTrades = params.get("trade") === "all";
  const all = data?.rows ?? [];
  const nameOk = (r: ScreenerRow) => allNames || r.liquid !== false;
  const earningsOk = (r: ScreenerRow) => allEarnings || r.earnings !== "in-cycle";
  const tradeOk = (r: ScreenerRow) => allTrades || !isThin(r);
  const shown = all.filter((r) => nameOk(r) && earningsOk(r) && tradeOk(r) && (q === "" || r.symbol.includes(q)));
  const heldOut = all.filter((r) => r.liquid === false).length;
  const inCycle = all.filter((r) => r.earnings === "in-cycle" && nameOk(r)).length;
  const thin = all.filter((r) => isThin(r) && nameOk(r) && earningsOk(r)).length;
  const unread = shown.filter((r) => r.recommendation === null).length;
  const sorted = sortRows(shown, sort, SORTS);
  const { page, setOffset, setLimit } = usePage([list, data?.day, allNames, allEarnings, allTrades, q, sort.key, sort.dir], 100);
  const rows = sorted.slice(page.offset, page.offset + page.limit);
  const spread = list === "credit-spreads";
  const latest = data?.days[0] ?? null;

  return (
    <section className="card">
      <div className="card-head">
        <h2>{LIST_TITLE[list]}</h2>
        <span className="chip" title="These rows are the vendor's screen as the evening capture saved them, not a list of ours">
          the vendor's list
        </span>
        <span className="chip">read-only</span>
        {data?.day && (
          <span className="card-asof">
            list of {data.day}
            {data.pricesAt ? ` · priced ${data.pricesAt}` : data.createdAt ? ` · built ${data.createdAt}` : ""}
            {data.tradeDay ? ` · trades read ${data.tradeDay}` : ""}
          </span>
        )}
      </div>

      <p className="muted" style={{ margin: "0 0 10px" }}>
        <strong>No tested edge.</strong> {VENDOR_NOTE[list]} The vendor's subscription ends on {VENDOR_END}: no list is saved
        after it, and this tab freezes on the last one.
      </p>

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
          title="Hide names with an announcement between today and the trade's expiry: the listed one when it is the trade, the cycle monthly (45-60 days out, else 30-60) when that is, the later of the two until a liquidity reading says which. An unknown date stays shown, marked unknown."
        >
          No earnings in cycle
        </Flag>
        <Flag
          on={!allTrades}
          onChange={(on) => set("trade", on ? null : "all")}
          title="Hide names with no liquid trade: the listed option is thin and so is the same strike on the cycle monthly (10% of mid or a nickel and 100 open; a spread judged as one order, its cost to trade within 10% of its credit, 100 open a leg). A name not yet read stays shown, marked no reading."
        >
          Liquid trade
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
          {!allTrades && thin > 0 ? ` · ${thin} with no liquid trade hidden` : ""}
          {unread > 0 ? ` · ${unread} not yet read for liquidity` : ""}
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
                    <SortTh label="Credit %" k="creditPct" sort={sort} className="num" title="Credit as a share of the width; the list's floor is a third" />
                  </>
                ) : (
                  <>
                    <SortTh label="Raw" k="raw" sort={sort} className="num" title="Mid ÷ share price" />
                    <SortTh label="Annual" k="ann" sort={sort} className="num" title="Raw × 365 ÷ days, simple" />
                    <SortTh label="Distance" k="dist" sort={sort} className="num" title="Strike against the share price" />
                  </>
                )}
                <SortTh label="Earnings" k="earnings" sort={sort} title="The next announcement, judged against the trade's expiry" />
                <th title="The listed option when it is liquid, else the same strike on the cycle monthly; 'thin' when that monthly fails the same bars">
                  Trade
                </th>
                <SortTh
                  label={spread ? "Trade credit" : "Trade prem"}
                  k="tradePrice"
                  sort={sort}
                  className="num"
                  title="The trade's price per share at the working price: halfway from the mid to the natural fill, from the regular-hours reading"
                />
                <SortTh
                  label={spread ? "Trade %" : "Trade annual"}
                  k="tradeReturn"
                  sort={sort}
                  className="num"
                  title={
                    spread
                      ? "The trade's working credit as a share of its width; red under the list's floor of a third"
                      : "The trade's working premium ÷ share price × 365 ÷ days, simple, as the list states its own"
                  }
                />
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
                      <td className="num">{pct(r.creditPct)}</td>
                    </>
                  ) : (
                    <>
                      <ListedReturnCell r={r} v={r.rawReturnPct} digits={2} />
                      <ListedReturnCell r={r} v={r.annualisedPct} />
                      <td className="num">{signedPct(r.distancePct)}</td>
                    </>
                  )}
                  <EarningsCell r={r} />
                  <TradeCell r={r} />
                  <TradePriceCell r={r} />
                  <TradeReturnCell r={r} />
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
        hours, else the same strike on the standard monthly you trade, priced from that reading at the working price
        (halfway from the mid to the natural fill: a limit at the mid stepped down a quarter of the spread). The listed
        columns are the list's own, at the mid of an after-close quote; a row whose quote has no bid, or is wider than its
        mid, says so instead of a yield.
      </p>
    </section>
  );
}
