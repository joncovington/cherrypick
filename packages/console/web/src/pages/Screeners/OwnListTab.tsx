import type { ReactNode } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import type { FairValueFields, IncomeList, IncomeListRow } from "@console/shared";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { FilterGroup, FilterSwitch, SortSection, sortRows, useSortChoice, type SortOption } from "../../components/FilterSort";
import { PremiumSlider, defaultFloor, useMinPremium } from "../../components/PremiumSlider";
import { Pager, usePage } from "../../components/ScopeBar";
import { useViewChoice } from "../../components/ViewSwitch";
import { useIncomeList } from "../../lib/api";
import { writePref } from "../../lib/prefs";
import { TradeCards, dayMonth, type TradeCardModel } from "../Live/TradeCard";
import { ScreenerCard, ScreenerLayout, ScreenerSection, SessionSelect, SymbolSearch, Tabs, useParamState } from "./ScreenerParts";

/**
 * Our own short-put and covered-call lists (2026-10-19): one row a name from the suite's universe,
 * the strike nearest 16 delta, priced at the working price. Every figure is `packages/technicals`
 * `income-lists`'; this formats and filters, and ranks nothing -- rows are by symbol, because the
 * rule has no tested edge and a yield order would read as one. The amber flag says so and stays while
 * the list shows; its text is behind the ?.
 *
 * Redesign (2026-11-06): each name is a trade card (rows on request) like the portfolio tab's adding
 * trades; the quote and the fair-value gap sit in the card's foot. The two lists are tabs in the card
 * head, and the liquid, earnings and fit filters, the premium floor, the sort and the symbol search
 * sit behind filter & sort.
 */

const TITLE: Record<IncomeList, string> = { "short-puts": "Short puts", "covered-calls": "Covered calls" };
const PAGE_SIZES = [24, 48, 96, 192] as const;

const NOTE: Record<IncomeList, string> = {
  "short-puts":
    "In the history study (52 names, 2020–2026, at this working price, after costs) a 16-delta put on " +
    "the 45–60 day monthly earned about what its market exposure earned and close to nothing beyond it; buying it " +
    "back at half the credit did a little better, about +1% a year at the median. It is the least-bad rule tested, " +
    "not an edge. Checked again on full option history (2025–26 and 2022, 2026-10-23): still no tested edge; what " +
    "gain there was came from the rising year.",
  "covered-calls":
    "In the history study (16 names, 2020–2026, after costs) writing a call against the shares lost " +
    "to simply holding them on most names, worse at higher deltas and on 45–60 day expiries; a 16-delta call on the " +
    "30–45 day monthly lost least, and SPY was the only name with a consistent gain. On full option history " +
    "(2025–26 and 2022, 2026-10-23) that call passed the declared test, but 2022 carried it and 2025–26 was a coin " +
    "flip: the overlay pays in falling years by giving up the upside. Read it as not refuted, not as an edge.",
};

const HEAD: Record<IncomeList, string> = { "short-puts": "No tested edge", "covered-calls": "Not refuted, not an edge" };
const FLAG: Record<IncomeList, string> = { "short-puts": "no tested edge", "covered-calls": "not refuted, not an edge" };

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

/** The next announcement against the trade's own expiry, in words; shared with the credit-spread tab. */
export function earningsText(r: Pick<IncomeListRow, "earnings" | "earningsDate" | "earningsEstimated">): string {
  if (r.earnings === "none") return "a fund: no earnings";
  const when = r.earningsDate ? `${shortDate(r.earningsDate)}${r.earningsEstimated ? " (est.)" : ""}` : "";
  if (r.earnings === "in-trade") return `earnings in the trade · ${when}`;
  if (r.earnings === "clear") return `earnings after expiry · ${when}`;
  // A date before the session is the report just made: the broker has not posted the next one yet.
  if (r.earningsDate) return `earnings past · ${shortDate(r.earningsDate)}, next not posted`;
  return "earnings date unknown";
}

export const FAIR_TITLE =
  "The session's DCF estimate of the share's value (cherrypick.core.valuation on Dolt's annual statements): the price " +
  "against it, with the model's low-high range. A model estimate, tested 2018–26: no edge, never a ranking.";

const FAIR_STATE: Record<string, string> = {
  "not-applicable": "a fund, a bank or a REIT: no fair value",
  "no-statements": "no annual statements by the session",
  refused: "the model refused this name",
  unbuilt: "no fair-value file for the session",
  unlisted: "not in the fair-value file",
};

/** The fair-value gap in words: the price against the model's value, `†` where the builder flagged it;
 *  shared with the credit-spread tab. */
export function fairText(fair: FairValueFields | null | undefined): string {
  if (fair === null || fair === undefined) return "no fair value";
  if (fair.fairState !== "valued" || fair.fairGap === null || fair.fairValue === null) return FAIR_STATE[fair.fairState ?? "unbuilt"] ?? "no fair value";
  return `vs fair ${signedPct(fair.fairGap)}${fair.fairFlags.length > 0 ? " †" : ""}`;
}

/** The covered call's ex-dividend date and its state, in words. A list built before the state was
 *  recorded carries only the in-trade flag: a flagged row reads in trade, anything else unknown -- a
 *  false there never meant clear. */
function exDividendText(r: IncomeListRow): string {
  const when = r.exDividendDate ? shortDate(r.exDividendDate) : "";
  const state = r.exDividend ?? (r.exDividendInTrade ? "in-trade" : "unknown");
  if (state === "in-trade") return `ex-dividend in the trade${when ? ` · ${when}` : ""}`;
  if (state === "after") return `ex-dividend after expiry · ${when}`;
  if (state === "past") return `ex-dividend past · ${when}`;
  return "ex-dividend unknown";
}

/**
 * One name as a trade card: the credit for one contract at the working price as the headline, the
 * strike's distance from the share as the bar (against the farthest on the list), the order that would
 * open it, and the quote, earnings and fair-value gap in the foot. Money follows the suite layout.
 */
export function incomeModel(r: IncomeListRow, calls: boolean, farthest: number): TradeCardModel {
  const right = calls ? "C" : "P";
  const dist = Math.abs(r.distanceFraction);
  const price = r.working === null ? "—" : `${money(r.working)} cr`;
  const q = [r.bid === null && r.ask === null ? "" : `${r.bid === null ? "—" : money(r.bid)} / ${r.ask === null ? "—" : money(r.ask)}`, `Δ ${Math.abs(r.delta).toFixed(2)}`, r.openInterest === null ? "" : `OI ${r.openInterest.toLocaleString("en-US")}`].filter(Boolean).join(" · ");
  const fund = r.type !== null && r.type !== "Stock";
  return {
    key: r.symbol,
    symbol: r.symbol,
    spot: money(r.spot),
    shape: `${calls ? "covered call" : "short put"} · ${r.dte} d${fund ? ` · ${r.type === "Exchange Traded Fund" ? "fund" : r.type}` : ""}`,
    chips: (
      <>
        {r.fit === true ? <span className="chip chip-ok">fits</span> : null}
        {r.fit === false ? (
          <span className="chip chip-warn" title={calls ? "No uncovered 100-share lot held" : "More buying power than the account has"}>
            does not fit
          </span>
        ) : null}
        {r.passes === false ? (
          <span className="chip chip-warn" title={`OI ${r.openInterest ?? "—"}, volume ${r.volume ?? "—"}${r.quiet ? " (quiet)" : ""}`}>
            thin
          </span>
        ) : null}
        {r.passes === null ? <span className="chip">unquoted</span> : null}
        {r.leveraged ? (
          <span className="chip" title="A daily-reset fund (the declared leveraged, inverse and volatility list): it decays and gaps in ways its delta does not price">
            {r.leveraged}
          </span>
        ) : null}
        {calls && r.window === "cycle" ? (
          <span className="chip" title="No monthly was listed 30-45 days out">
            cycle monthly
          </span>
        ) : null}
      </>
    ),
    cash: r.premium,
    side: [`${price} at the working price`, `${r.dte} d · ${dayMonth(r.expiry)}`],
    bar: farthest > 0 ? (dist / farthest) * 100 : null,
    barTitle: "how far the strike sits from the share price, against the farthest on this list",
    label: `distance ${signedPct(r.distanceFraction)} to the strike`,
    open: { date: "proposed", price },
    legs: [{ buy: false, text: `1 ${r.expiry} ${r.strike}${right}${r.working === null ? "" : ` @ ${money(r.working)}`}`, quote: q || null }],
    after: [`annual ${pct(r.annualFraction)}`, `IV ${pct(r.iv, 0)}`, `return ${pct(r.returnFraction, 2)}`],
    foot: [calls ? `${earningsText(r)} · ${exDividendText(r)}` : earningsText(r), fairText(r.fair)],
  };
}

/** The income sorts: symbol (the default; no tested edge, so never by credit or yield), timing, how
 *  far out the strike sits, delta. */
export const INCOME_LIST_SORTS: SortOption<IncomeListRow>[] = [
  { id: "symbol", label: "symbol", hint: "A to Z", key: (r) => r.symbol },
  { id: "expiry", label: "expiry", hint: "soonest first", key: (r) => r.dte },
  { id: "distance", label: "distance", hint: "farthest first", key: (r) => -Math.abs(r.distanceFraction) },
  { id: "delta", label: "Δ", hint: "lowest first", key: (r) => Math.abs(r.delta) },
];

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

/** `?list=calls` or `?list=puts`, else the list an old tab's address names (`/screeners/covered-calls`). */
export function incomeListFrom(param: string | null, path: string): IncomeList {
  if (param === "calls") return "covered-calls";
  if (param === "puts") return "short-puts";
  return path.endsWith("/covered-calls") ? "covered-calls" : "short-puts";
}

/**
 * Both income lists on one tab (2026-10-30, docs/console-review-plan.md batch 3): the same component,
 * the same cards, one switch -- now two tabs in the card's head. The list rides `?list=`, and the two
 * old tabs' addresses still open their own list.
 */
export function IncomeListsTab() {
  const [params, setParams] = useSearchParams();
  const { pathname } = useLocation();
  const list = incomeListFrom(params.get("list"), pathname);
  const choose = (next: IncomeList) => {
    const p = new URLSearchParams(params);
    p.set("list", next === "covered-calls" ? "calls" : "puts");
    setParams(p, { replace: true });
  };
  return (
    <OwnListTab
      key={list}
      list={list}
      tabs={
        <Tabs
          label="income list"
          value={list}
          options={(["short-puts", "covered-calls"] as const).map((l) => ({ id: l, label: TITLE[l].toLowerCase() }))}
          onChange={choose}
        />
      }
    />
  );
}

/** The ? behind the list: the study that found no edge, where the names come from, and what was left out. */
export function incomeHelp(list: IncomeList, calls: boolean, dropped: [string, number][]): CardHelpContent {
  return {
    title: "Where these come from",
    body: (
      <>
        <HelpWarn title={HEAD[list]}>
          <p>{NOTE[list]}</p>
        </HelpWarn>
        <HelpSection title="Where they come from">
          <p>
            Our options-tradable universe (weekly options and $100M a day in the stock) and the funds that pass the same test.{" "}
            {calls
              ? "The call nearest 16 delta above the price, on the monthly 30-45 days out (else the cycle monthly), for shares you hold."
              : "The put nearest 16 delta below the price, on the cycle monthly (45-60 days out, else 30-60)."}{" "}
            Priced from the afternoon reading at the working price: re-quote before sending. The return is yield, not edge, and the cards are
            in symbol order on purpose.
          </p>
        </HelpSection>
        <HelpSection title="Counting what is hidden">
          <p>Each hidden name is counted once, under the first filter it fails, in the order the filters sit: liquid trade, earnings, fit, premium floor.</p>
        </HelpSection>
        <HelpSection title="Fair value">
          <p>{FAIR_TITLE} A dagger marks a flag from the builder.</p>
        </HelpSection>
        {dropped.length > 0 ? (
          <HelpSection title="Left out">
            <p>No row for: {dropped.map(([k, v]) => `${v} (${k})`).join(", ")}.</p>
          </HelpSection>
        ) : null}
      </>
    ),
  };
}

export function OwnListTab({ list, tabs }: { list: IncomeList; tabs?: ReactNode }) {
  const { params, set, clear } = useParamState();
  const dayParam = params.get("day");
  const { data, isLoading, isError } = useIncomeList(list, dayParam);
  const [sort, setSort] = useSortChoice(`income-${list}`, "symbol");
  const [view, setView] = useViewChoice(`income-${list}`);
  const allTrades = params.get("trade") === "all";
  const allEarnings = params.get("earnings") === "all";
  const allFit = params.get("fit") === "all";
  const q = (params.get("q") ?? "").trim().toUpperCase();

  const all = data?.rows ?? [];
  const minPremium = useMinPremium(list, params.get("premium"));
  const { shown, hidden } = applyFilters(all, { allTrades, allEarnings, allFit, minPremium, q });
  const { thin, inTrade, unfit, small, unmatched } = hidden;
  // Covered calls: manage judges a call unfit only for want of an uncovered 100-share lot, so when
  // every row is unfit the account holds none, and the empty list should say that plainly.
  const noLotHeld = list === "covered-calls" && all.length > 0 && all.every((r) => r.fit === false);
  const sorted = sortRows(shown, INCOME_LIST_SORTS.find((s) => s.id === sort.id), sort.reversed);
  const { page, setOffset, setLimit } = usePage([list, data?.day, allTrades, allEarnings, allFit, minPremium, q, sort.id, sort.reversed], 48);
  const rows = sorted.slice(page.offset, page.offset + page.limit);
  const calls = list === "covered-calls";
  const farthest = Math.max(0, ...shown.map((r) => Math.abs(r.distanceFraction)));
  const latest = data?.days[0] ?? null;
  const dropped = Object.entries(data?.dropped ?? {});

  const sortChanged = sort.id !== "symbol" || sort.reversed;
  const changed =
    (sortChanged ? 1 : 0) + (allTrades ? 1 : 0) + (allEarnings ? 1 : 0) + (data?.fitBasis && allFit ? 1 : 0) + (minPremium !== defaultFloor(list) ? 1 : 0) + (q !== "" ? 1 : 0);
  const reset = () => {
    void setSort({ id: "symbol", reversed: false });
    void writePref(`minPremium.${list}`, undefined);
    clear(["trade", "earnings", "fit", "premium", "q"]);
  };
  const sortLabel = INCOME_LIST_SORTS.find((s) => s.id === sort.id)?.label ?? sort.id;
  const hiddenTotal = thin + inTrade + unfit + small + unmatched;

  return (
    <ScreenerLayout>
      <ScreenerSection concept="income" tag="candidates" question={`Which ${calls ? "covered calls" : "short puts"} fit?`}>
        <ScreenerCard
          title={TITLE[list].toLowerCase()}
          help={incomeHelp(list, calls, dropped)}
          flag={FLAG[list]}
          extra={data ? <SessionSelect days={data.days} day={data.day} latest={latest} label="Session" onChange={(d) => set("day", d)} /> : null}
          tabs={tabs}
          view={{ value: view, onChange: setView }}
          filter={{
            changed,
            parts: [
              `by ${sortLabel}${sort.reversed ? ", reversed" : ""}`,
              ...(allTrades ? [] : ["liquid"]),
              ...(allEarnings ? [] : ["no earnings"]),
              ...(data?.fitBasis && !allFit ? ["fits"] : []),
              minPremium === 0 ? "any premium" : `≥ $${minPremium.toLocaleString()}`,
            ],
            note: data?.day
              ? `${shown.length} of ${all.length} names${
                  hiddenTotal > 0
                    ? ` · ${[
                        thin > 0 ? `${thin} thin` : "",
                        inTrade > 0 ? `${inTrade} with earnings in the trade` : "",
                        unfit > 0 ? `${unfit} that do not fit the account` : "",
                        small > 0 ? `${small} under $${minPremium.toLocaleString()}` : "",
                        unmatched > 0 ? `${unmatched} not matching “${q}”` : "",
                      ]
                        .filter(Boolean)
                        .join(" · ")} hidden`
                    : ""
                }`
              : null,
            onReset: reset,
            panel: (
              <>
                <SortSection sorts={INCOME_LIST_SORTS} choice={sort} fallback="symbol" onChange={setSort} />
                <FilterGroup title="filters">
                  <FilterSwitch
                    label="Liquid trade"
                    description="Hide an option that fails the leg bars in the afternoon reading (10% of mid or a nickel, 100 open). An unquoted one stays shown, marked."
                    on={!allTrades}
                    onChange={(on) => set("trade", on ? null : "all")}
                  />
                  <FilterSwitch
                    label="No earnings in the trade"
                    description="Hide names with an announcement between today and this trade's expiry. An unknown date stays shown, marked unknown."
                    on={!allEarnings}
                    onChange={(on) => set("earnings", on ? null : "all")}
                  />
                  {data?.fitBasis ? (
                    <FilterSwitch
                      label="Fits the account"
                      description={
                        calls
                          ? "Hide a call the account holds no uncovered 100-share lot for (shares already written against are spoken for)."
                          : `Hide a put whose buying power is more than the ${money(data.fitBasis.availableBp)} the account has (manage's ${data.fitBasis.session} evaluation, ${data.fitBasis.basis === "cash" ? "cash-secured" : "Reg T"}).`
                      }
                      on={!allFit}
                      onChange={(on) => set("fit", on ? null : "all")}
                    />
                  ) : null}
                </FilterGroup>
                <FilterGroup title="premium floor" note="one contract's credit">
                  <PremiumSlider
                    list={list}
                    value={minPremium}
                    onChange={(v) => set("premium", v === 0 ? null : String(v))}
                    hidden={small}
                    title="Hide a trade whose working premium, for the whole contract, is under this. Steps rather than a straight scale, so the small premiums where most rows sit get room. An unquoted option stays shown. Remembered for next time."
                  />
                </FilterGroup>
                <FilterGroup title="symbol">
                  <SymbolSearch value={params.get("q") ?? ""} onChange={(v) => set("q", v === "" ? null : v)} />
                </FilterGroup>
              </>
            ),
          }}
        >
          {data?.day && (
            <p className="muted pf-caption" style={{ marginTop: 0 }}>
              session {data.day}
              {data.chainTakenAt ? ` · quoted ${new Date(data.chainTakenAt).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit" })} ET` : ""}
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
          <TradeCards models={rows.map((r) => incomeModel(r, calls, farthest))} />
          {shown.length > PAGE_SIZES[0] && (
            <Pager offset={page.offset} limit={page.limit} total={shown.length} pageSizes={PAGE_SIZES} onOffset={setOffset} onLimit={setLimit} />
          )}
          {data && data.day !== null && all.length > 0 && shown.length === 0 && !(noLotHeld && !allFit) && (
            <p className="muted">Nothing matches these filters.</p>
          )}
        </ScreenerCard>
      </ScreenerSection>
    </ScreenerLayout>
  );
}
