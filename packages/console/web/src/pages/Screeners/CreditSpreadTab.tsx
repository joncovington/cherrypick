import { useMemo } from "react";
import type { CreditSpreadPrice, CreditSpreadRow } from "@console/shared";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { FilterGroup, FilterSwitch, SortSection, sortRows, useSortChoice, type SortOption } from "../../components/FilterSort";
import { Pager, usePage } from "../../components/ScopeBar";
import { useViewChoice } from "../../components/ViewSwitch";
import { useCreditSpreadList } from "../../lib/api";
import { TradeCards, dayMonth, type TradeCardModel } from "../Live/TradeCard";
import { FAIR_TITLE, earningsText, fairText } from "./OwnListTab";
import { BoxOpenButton, PanelChoice, ScreenerCard, ScreenerLayout, ScreenerSection, SessionSelect, SymbolSearch, useParamState, useScreenerBoxes, type ScreenerBox } from "./ScreenerParts";

/**
 * Our own credit-spread list (2026-10-24): the chart setups' fresh entries as spreads on the 30-45 day
 * monthly, from `packages/technicals` `income-lists` (version 2, `creditspreads.py`). A long setup is a
 * put spread, its short mirror a call spread; the short nearest 0.40 delta, the pick the widest long
 * giving 35-40% of the width at the working price. Every figure is the builder's; this formats and
 * filters, and ranks nothing -- rows are by symbol.
 *
 * NO TESTED EDGE: item 17 of docs/edge-hypotheses-plan.md (run 2026-10-25) found no setup, side, short
 * or width that passed after costs. The tab reads the rows by a SELECTION (the user's choice the same
 * day): the 35-40% credit rule (every row, the default) or one of the study's least-bad mean-reversion
 * put cells (0.20 or 0.40 delta, 5 strikes), which keep only their rows and show their own short and
 * spread (`rowsFor`).
 *
 * Redesign (2026-11-06): each setup is a trade card (rows on request) -- the chosen width, the credit,
 * the max loss as the bar, Cr/ML -- and its other widths and the setup's context open in a lightbox
 * from the card. The rule, side, earnings, liquid and width filters, the sort and the symbol search
 * sit behind filter & sort; the item-17 verdict is the amber flag, its text behind the ?.
 */

const PAGE_SIZES = [24, 48, 96, 192] as const;

const SETUP_LABEL: Record<string, string> = {
  trend: "trend",
  pullback: "pullback",
  reversion: "mean reversion",
  breakout: "breakout",
  "trend-short": "trend (short)",
  "pullback-short": "pullback (short)",
  "reversion-short": "mean reversion (short)",
  "breakout-short": "breakdown",
};

function money(v: number | null): string {
  return v === null ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pct(v: number | null, digits = 1): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

function shortDate(iso: string | null): string {
  if (iso === null || iso === "") return "—";
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** Per share at the working price, signed cash flow: a credit `cr`, a price under zero a debit `db`. */
function credit(v: number): string {
  return `${money(Math.abs(v))} ${v < 0 ? "db" : "cr"}`;
}

/** Whole contract: a per-share figure × 100. */
function contract(v: number | null): string {
  return v === null ? "—" : `$${(v * 100).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

/** The call spread's ex-dividend date against expiry, in words; a put spread has none. */
function exDividendText(r: CreditSpreadRow): string | null {
  if (r.spread === "put") return null;
  const when = r.exDividendDate ? shortDate(r.exDividendDate) : "";
  if (r.exDividend === "in-trade") return when ? `in trade · ${when}` : "in trade";
  if (r.exDividend === "after") return `after · ${when}`;
  if (r.exDividend === "past") return `past · ${when}`;
  return "unknown";
}

function setupsText(r: CreditSpreadRow): string {
  return r.signals.map((s) => `${SETUP_LABEL[s.setup] ?? s.setup} · ${s.ago === 0 ? "last close" : `${s.ago}d ago`}`).join(", ");
}

function LiquidCell({ p }: { p: CreditSpreadPrice | null }) {
  if (p === null || p.passes === null) return <td className="muted">—</td>;
  return (
    <td className={p.passes ? undefined : "muted"} title="The leg bars on the spread as one order: every leg two-sided with its open interest, and its cost to trade within the bars">
      {p.passes ? "liquid" : "thin"}
    </td>
  );
}

/** The setup's context: the setups behind the row and the close each entered on, the price, the fair
 *  value gap and the ex-dividend date. */
export function SetupBody({ r }: { r: CreditSpreadRow }) {
  const ex = exDividendText(r);
  return (
    <table className="data-table secondary-cells" aria-label={`${r.symbol} setups and context`}>
      <thead>
        <tr>
          <th title="The setups behind the row, and the close each entered on">Setups</th>
          <th className="num">Price</th>
          <th title={FAIR_TITLE}>Vs fair</th>
          <th title="Call spreads: the broker's ex-dividend date against expiry">Ex-div</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td title={r.signals.map((s) => `${SETUP_LABEL[s.setup] ?? s.setup}: entered on the ${s.date} close`).join("\n")}>{setupsText(r)}</td>
          <td className="num">{money(r.spot)}</td>
          <td className="muted">{fairText(r.fair).replace(/^vs fair /, "")}</td>
          <td className={ex === null ? "muted" : undefined}>{ex ?? "—"}</td>
        </tr>
      </tbody>
    </table>
  );
}

/** The width table: every declared width, both units, as the builder priced it. */
export function WidthsBody({ r }: { r: CreditSpreadRow }) {
  const right = r.spread === "put" ? "P" : "C";
  return (
    <div className="table-scroll">
      <table className="data-table" aria-label={`${r.symbol} ${r.spread} spread widths`}>
        <thead>
          <tr>
            <th>Width</th>
            <th className="num">Long</th>
            <th className="num">Δ</th>
            <th className="num">Width $</th>
            <th className="num" title="Per share at the working price">Credit</th>
            <th className="num" title="Per share at the mid">Mid</th>
            <th className="num" title="Credit ÷ width">Credit %</th>
            <th className="num" title="Whole contract: (width − credit) × 100">Max loss</th>
            <th className="num" title="Credit ÷ max loss">Cr / ML</th>
            <th>Liquid</th>
          </tr>
        </thead>
        <tbody>
          {r.widths.map((w) => {
            const p = w.price;
            const label = w.unit === "strikes" ? `${w.size} strike${w.size === 1 ? "" : "s"}` : `${w.size} × move`;
            const picked = p !== null && r.pick !== null && p.long === r.pick.long;
            return (
              <tr key={`${w.unit}-${w.size}`}>
                <td title={w.unit === "move" && r.move !== null ? `The listed long nearest ${w.size} × the 1-SD move (${money(r.move)}) beyond the short` : undefined}>
                  {label}
                  {picked ? (
                    <>
                      {" "}
                      <span className="chip" title="The same long as the 35-40% pick">
                        pick
                      </span>
                    </>
                  ) : null}
                </td>
                {p === null ? (
                  <td colSpan={9} className="muted">
                    the chain does not reach this width
                  </td>
                ) : (
                  <>
                    <td className="num">
                      {p.long}
                      {right}
                    </td>
                    <td className="num">{p.longDelta.toFixed(2)}</td>
                    <td className="num">{money(p.width)}</td>
                    <td className="num">{credit(p.credit)}</td>
                    <td className="num">{credit(p.creditMid)}</td>
                    <td className="num">{pct(p.creditWidth)}</td>
                    <td className="num">{contract(p.maxLoss)}</td>
                    <td className="num">{pct(p.creditMaxLoss)}</td>
                    <LiquidCell p={p} />
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export interface CreditSpreadFilters {
  side: "both" | "put" | "call";
  allEarnings: boolean;
  allTrades: boolean;
  pickedOnly: boolean;
  q: string;
}

/**
 * The rows shown and why each hidden row is hidden, layered in the order the filters sit -- side,
 * earnings, liquid, picked, then the symbol search -- so a row failing several is counted once, under
 * the first. A row with no pick has no spread to judge for liquidity, so only "with a pick" hides it.
 */
export function applyFilters(all: CreditSpreadRow[], f: CreditSpreadFilters) {
  const hidden = { side: 0, inTrade: 0, thin: 0, unpicked: 0, unmatched: 0 };
  const shown: CreditSpreadRow[] = [];
  for (const r of all) {
    if (f.side !== "both" && r.spread !== f.side) hidden.side += 1;
    else if (!f.allEarnings && r.earnings === "in-trade") hidden.inTrade += 1;
    else if (!f.allTrades && r.pick !== null && r.pick.passes === false) hidden.thin += 1;
    else if (f.pickedOnly && r.pick === null) hidden.unpicked += 1;
    else if (f.q !== "" && !r.symbol.includes(f.q)) hidden.unmatched += 1;
    else shown.push(r);
  }
  return { shown, hidden };
}

/** The sorts: symbol (the default), then size and width; never credit or Cr/ML (no tested edge). */
export const CREDIT_SORTS: SortOption<CreditSpreadRow>[] = [
  { id: "symbol", label: "symbol", hint: "A to Z", key: (r) => r.symbol },
  { id: "maxloss", label: "max loss", hint: "largest first", key: (r) => (r.pick === null ? null : -r.pick.maxLoss) },
  { id: "width", label: "width", hint: "narrowest first", key: (r) => r.pick?.width ?? null },
  { id: "expiry", label: "expiry", hint: "soonest first", key: (r) => r.dte },
];

/** The default selection: the user's original rule, which reads every row. */
export const DEFAULT_SELECTION = "credit-share";

/** The rows as one selection reads them: the credit rule keeps every row as built; any other keeps
 *  only the rows that selection lists, each carrying that selection's short and spread in place of the
 *  row's, so every column, filter and sort reads the selected rule. */
export function rowsFor(rows: CreditSpreadRow[], selection: string): CreditSpreadRow[] {
  if (selection === DEFAULT_SELECTION) return rows;
  return rows.flatMap((r) => {
    const s = r.selections?.[selection];
    return s ? [{ ...r, short: s.short, pick: s.price }] : [];
  });
}

export const boxId = (r: CreditSpreadRow) => `${r.symbol}-${r.spread}`;

/**
 * One setup as a trade card: the chosen width's credit as the headline, the max loss as the bar
 * (against the largest on the list: no per-trade cap comes with the list), the order that would open
 * it, the setup and the earnings in the foot. `open` is the card's button for the lightbox.
 */
export function creditModel(r: CreditSpreadRow, shareLabel: string, largest: number, open: ((id: string) => void) | null): TradeCardModel {
  const right = r.spread === "put" ? "P" : "C";
  const p = r.pick;
  const mid = r.short.bid !== null && r.short.ask !== null ? ` @ ${((r.short.bid + r.short.ask) / 2).toFixed(2)}` : "";
  const ex = exDividendText(r);
  return {
    key: boxId(r),
    symbol: r.symbol,
    symbolHref: `/charts/technicals?symbol=${encodeURIComponent(r.symbol)}`,
    spot: money(r.spot),
    shape: `${r.spread === "put" ? "bull put" : "bear call"} · ${r.dte} d · ${dayMonth(r.expiry)}`,
    chips: (
      <>
        {p === null ? (
          <span className="chip chip-warn" title="No listed width gives that share of the width at the working price; open the widths for each one's credit">
            no {shareLabel} width
          </span>
        ) : (
          <span className="chip chip-ok">{shareLabel} width</span>
        )}
        {p !== null && p.passes === false ? <span className="chip chip-warn">thin</span> : null}
        {r.window === "45-60" ? (
          <span className="chip chip-warn" title="No standard monthly fell 30-45 days out, so this is the 45-60 day monthly: outside the window item 17 studied">
            45-60
          </span>
        ) : null}
        {r.conflict ? (
          <span className="chip chip-warn" title="The name signals both sides at once: a put spread and a call spread">
            both sides
          </span>
        ) : null}
        {r.leveraged ? (
          <span className="chip" title="A daily-reset fund: it decays and gaps in ways its delta does not price">
            {r.leveraged}
          </span>
        ) : null}
      </>
    ),
    cash: p === null ? null : p.credit * 100,
    side: p === null ? ["no pick", `${r.dte} d`] : [credit(p.credit), `Cr/ML ${p.creditMaxLoss === null ? "—" : p.creditMaxLoss.toFixed(2)}`],
    bar: p === null || largest <= 0 ? null : (p.maxLoss * 100 * 100) / largest,
    barTitle: "the max loss, against the largest on this list: the list carries no per-trade cap",
    label: p === null ? "no width to size" : `max loss ${contract(p.maxLoss)}${p.creditWidth === null ? "" : ` · credit ${pct(p.creditWidth, 0)} of the width`}`,
    open: { date: "proposed", price: p === null ? "—" : credit(p.credit) },
    legs: [
      {
        buy: false,
        text: `1 ${r.expiry} ${r.short.strike}${right}${mid}`,
        quote: `${r.short.bid === null ? "—" : r.short.bid.toFixed(2)} / ${r.short.ask === null ? "—" : r.short.ask.toFixed(2)} · Δ ${Math.abs(r.short.delta).toFixed(2)}`,
      },
      ...(p === null ? [] : [{ buy: true, text: `1 ${r.expiry} ${p.long}${right}`, quote: `Δ ${Math.abs(p.longDelta).toFixed(2)}` }]),
    ],
    after: [`setup: ${setupsText(r)}`],
    foot: [`${earningsText(r)}${ex === null ? "" : ` · ex-div ${ex}`}`, fairText(r.fair)],
    extra: open ? <BoxOpenButton onOpen={() => open(boxId(r))}>{r.symbol} · other widths</BoxOpenButton> : undefined,
  };
}

export function creditHelp(tested: string | null, selection: { label: string; rule: string } | undefined, shareLabel: string, dropped: [string, number][], recent: number, unpicked: number, total: number): CardHelpContent {
  return {
    title: "Where these come from",
    body: (
      <>
        <HelpWarn title="No tested edge">
          <p>
            Item 17 of the edge-hypotheses plan tested every setup, side, short and width on 2020-2025 and none passed after costs
            {tested ? ` (${tested})` : ""}. The selections are rules to read the cards by, not recommendations: the 35-40% credit rule you
            trade, and the two mean-reversion put cells the study found least bad.
            {selection && selection.label ? ` Showing: ${selection.rule}.` : ""}
          </p>
        </HelpWarn>
        <HelpSection title="Where they come from">
          <p>
            The names are our options-tradable universe. A card is a chart setup that entered on one of the last {recent} closes and is
            still open: trend, pullback, mean reversion or breakout as a put spread, and each one's short mirror as a call spread. The
            spread is on the standard monthly 30–45 days out, the short nearest 0.40 delta and the long the widest giving {shareLabel} of
            the width at the working price{unpicked > 0 ? ` (${unpicked} of ${total} have no such width)` : ""}.
          </p>
        </HelpSection>
        <HelpSection title="Widths and money">
          <p>
            Open a card's other widths for 1, 2, 3 and 5 strikes, and a quarter, a half and three quarters of the expected move. Credits
            are per share at the working price; max loss is per contract.
          </p>
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

export function CreditSpreadTab() {
  const { params, set, clear } = useParamState();
  const { data, isLoading, isError } = useCreditSpreadList(params.get("day"));
  const [sort, setSort] = useSortChoice("credit-spreads", "symbol");
  const [view, setView] = useViewChoice("credit-spreads");
  const sideParam = params.get("side");
  const side: CreditSpreadFilters["side"] = sideParam === "put" || sideParam === "call" ? sideParam : "both";
  const allEarnings = params.get("earnings") === "all";
  const allTrades = params.get("trade") === "all";
  const pickedOnly = params.get("picked") === "only";
  const q = (params.get("q") ?? "").trim().toUpperCase();

  const selections = data?.rule.selections ?? [];
  const selection = selections.some((s) => s.id === params.get("selection")) ? (params.get("selection") as string) : DEFAULT_SELECTION;
  const current = selections.find((s) => s.id === selection);
  const all = useMemo(() => rowsFor(data?.rows ?? [], selection), [data, selection]);
  const { shown, hidden } = applyFilters(all, { side, allEarnings, allTrades, pickedOnly, q });
  const sorted = sortRows(shown, CREDIT_SORTS.find((s) => s.id === sort.id), sort.reversed);
  const { page, setOffset, setLimit } = usePage([data?.day, selection, side, allEarnings, allTrades, pickedOnly, q, sort.id, sort.reversed], 48);
  const rows = sorted.slice(page.offset, page.offset + page.limit);
  const latest = data?.days[0] ?? null;
  const dropped = Object.entries(data?.dropped ?? {});
  const share = data?.rule.creditShare ?? [0.35, 0.4];
  const shareLabel = `${Math.round(share[0] * 100)}–${Math.round(share[1] * 100)}%`;
  const unpickedAll = all.filter((r) => r.pick === null).length;
  const largest = Math.max(0, ...shown.map((r) => (r.pick === null ? 0 : r.pick.maxLoss * 100)));

  // The lightbox: every shown setup's widths and context, in the card's order, so ← → walk the list.
  const boxes: ScreenerBox[] = sorted.map((r) => ({
    id: boxId(r),
    kicker: "credit spreads ›",
    title: `${r.symbol} · ${r.spread === "put" ? "bull put" : "bear call"} widths`,
    tabs: [
      { id: "widths", label: "widths" },
      { id: "setup", label: "the setup" },
    ],
    body: (tab) => (tab === "setup" ? <SetupBody r={r} /> : <WidthsBody r={r} />),
  }));
  const box = useScreenerBoxes(boxes);

  const sortChanged = sort.id !== "symbol" || sort.reversed;
  const changed =
    (sortChanged ? 1 : 0) + (selection !== DEFAULT_SELECTION ? 1 : 0) + (side !== "both" ? 1 : 0) + (allEarnings ? 1 : 0) + (allTrades ? 1 : 0) + (pickedOnly ? 1 : 0) + (q !== "" ? 1 : 0);
  const reset = () => {
    void setSort({ id: "symbol", reversed: false });
    clear(["selection", "side", "earnings", "trade", "picked", "q"]);
  };
  const sortLabel = CREDIT_SORTS.find((s) => s.id === sort.id)?.label ?? sort.id;
  const hiddenParts = [
    hidden.side > 0 ? `${hidden.side} on the other side` : "",
    hidden.inTrade > 0 ? `${hidden.inTrade} with earnings in the trade` : "",
    hidden.thin > 0 ? `${hidden.thin} thin` : "",
    hidden.unpicked > 0 ? `${hidden.unpicked} with no ${shareLabel} width` : "",
    hidden.unmatched > 0 ? `${hidden.unmatched} not matching “${q}”` : "",
  ].filter(Boolean);

  return (
    <ScreenerLayout>
      <ScreenerSection concept="dir" tag="setups" question="Which setups make a spread worth sizing?">
        <ScreenerCard
          title="credit spreads"
          help={creditHelp(data?.rule.tested ?? null, current, shareLabel, dropped, data?.rule.recentSessions ?? 5, unpickedAll, all.length)}
          flag="no tested edge"
          extra={data ? <SessionSelect days={data.days} day={data.day} latest={latest} label="Session" onChange={(d) => set("day", d)} /> : null}
          view={{ value: view, onChange: setView }}
          filter={{
            changed,
            parts: [current ? current.label : `${shareLabel} credit`, side === "both" ? "both sides" : side === "put" ? "put spreads" : "call spreads", `by ${sortLabel}${sort.reversed ? ", reversed" : ""}`],
            note: `${shown.length} setups of ${all.length}${hiddenParts.length > 0 ? ` · ${hiddenParts.join(" · ")} hidden` : ""}`,
            onReset: reset,
            panel: (
              <>
                {selections.length > 0 ? (
                  <FilterGroup title="rule" note="the rule the cards are read by">
                    <PanelChoice
                      label="Selection"
                      value={selection}
                      options={selections.map((s) => ({ id: s.id, label: s.label, title: s.rule }))}
                      onChange={(id) => set("selection", id === DEFAULT_SELECTION ? null : id)}
                    />
                  </FilterGroup>
                ) : null}
                <FilterGroup title="side">
                  <PanelChoice
                    label="Side"
                    value={side}
                    options={[
                      { id: "both", label: "both" },
                      { id: "put", label: "put spreads" },
                      { id: "call", label: "call spreads" },
                    ]}
                    onChange={(s) => set("side", s === "both" ? null : s)}
                  />
                </FilterGroup>
                <FilterGroup title="filters">
                  <FilterSwitch
                    label="No earnings in the trade"
                    description="Hide names with an announcement between today and this spread's expiry. An unknown date stays shown, marked unknown."
                    on={!allEarnings}
                    onChange={(on) => set("earnings", on ? null : "all")}
                  />
                  <FilterSwitch
                    label="Liquid trade"
                    description="Hide a picked spread that fails the leg bars as one order. A setup with no pick stays shown: it has no spread to judge."
                    on={!allTrades}
                    onChange={(on) => set("trade", on ? null : "all")}
                  />
                  <FilterSwitch
                    label={`With a ${shareLabel} width`}
                    description={`Show only names where some listed width gives ${shareLabel} of the width at the working price. Off by default: most names have none, and their widths say how near each comes.`}
                    on={pickedOnly}
                    onChange={(on) => set("picked", on ? "only" : null)}
                  />
                </FilterGroup>
                <SortSection sorts={CREDIT_SORTS} choice={sort} fallback="symbol" onChange={setSort} />
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
          {data?.chainComplete === false && (
            <p className="muted" style={{ margin: "0 0 8px" }}>
              The afternoon reading did not finish{data.chainProblem ? ` (${data.chainProblem})` : ""}, so some names are missing.
            </p>
          )}
          {isError && <p className="muted">Could not read the list.</p>}
          {isLoading && <p className="muted">Reading the list…</p>}
          {data && data.day === null && (
            <p className="muted">
              No list built yet: the afternoon chains are read at 14:45 ET on a trading day and the list is built from them at 15:10.
            </p>
          )}
          {data && data.day !== null && !data.built && (
            <p className="muted">
              No credit-spread list for this session: it was built before the list existed (2026-10-24), or without the history
              store the setups are read from.
            </p>
          )}
          <TradeCards models={rows.map((r) => creditModel(r, shareLabel, largest, box.open))} />
          {shown.length > PAGE_SIZES[0] && (
            <Pager offset={page.offset} limit={page.limit} total={shown.length} pageSizes={PAGE_SIZES} onOffset={setOffset} onLimit={setLimit} />
          )}
          {data && data.built && all.length > 0 && shown.length === 0 && <p className="muted">Nothing matches these filters.</p>}
          {data && data.built && all.length === 0 && <p className="muted">No setup entered on a recent close for any name this session.</p>}
        </ScreenerCard>
      </ScreenerSection>
      {box.node}
    </ScreenerLayout>
  );
}
