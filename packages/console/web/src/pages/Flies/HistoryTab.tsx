import { Fragment, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { useFliesTradeLog, fliesQuery, type FliesFilter, type FliesTradeLogRow } from "../../lib/api";
import { useColumnLayout, type ColumnDef } from "../../components/table/columns";
import { ColumnsMenu } from "../../components/table/ColumnsMenu";
import { DateRangeBar, useUrlDateRange } from "../../components/table/DateRange";
import { Card, DataCard, PnlCell, SkeletonRows, SortTh, fmtMoney, fmtNum, sortRows, useSort, type SortHeader } from "../../components/DataTable";
import { Pager, usePage } from "../../components/ScopeBar";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { ListViewContext, ViewSwitch, useViewChoice, type ListView } from "../../components/ViewSwitch";
import { FilterGroup, FilterSortButton, FilterSortPanel, FilterSummary, SortSection, type SortChoice, type SortOption } from "../../components/FilterSort";
import { CardLightbox, LightboxFrame, useOpenParam } from "../Backtest/CardLightbox";
import { TradeCard, TradeCards, type TradeCardModel } from "../Live/TradeCard";
import { ChoiceGroup, FliesLayout, FliesSection } from "./FliesFilter";
import { clockTime, structureLabel, wingWidth } from "./structure";
import { fmtCash, fmtPrice } from "../../lib/format";

interface Summary {
  trades: number;
  sessions: number;
  grossPnl: number;
  /** Trading fees; settlement is its own figure. */
  fees: number;
  /** Null (n/r) when any trade behind it never recorded settlement. */
  settlementFees: number | null;
  premium: number;
  netPnl: number;
  wins: number;
  losses: number;
  winRatePct: number | null;
  avgPnl: number | null;
  feeDragPct: number | null;
  profitFactor: number | null;
}

interface CompletionSummary {
  trades: number;
  completed: number;
  completionPct: number | null;
  medianLatencyMin: number | null;
  pinned: number;
  pinnedPct: number | null;
}

interface History {
  /** The regime-cuts writer's thin floor; null when it has published none (see readers/flies.ts). */
  thinBelowSessions: number | null;
  byArm: Array<{ arm: string } & Summary>;
  byEntryMode: Array<{ entryMode: string } & Summary>;
  byEntryHour: Array<{ hour: string } & Summary>;
  byArmHour: Array<{ arm: string; hour: string } & CompletionSummary>;
  feeDrag: Array<{ arm: string } & Summary>;
  dailyPnl: Array<{ date: string; trades: number; netPnl: number }>;
}

/** "10:00-11:00" -> "10-11", so the hour matrix stays narrow enough to need no horizontal scroll. */
function shortHour(hour: string): string {
  const m = /^(\d{2}):00-(\d{2}):00$/.exec(hour);
  return m ? `${m[1]}–${m[2]}` : hour;
}

function useHistory(mode: TradingMode, filter: FliesFilter, from: string | null, to: string | null) {
  return useQuery<History>({
    queryKey: ["flies-history", mode, filter.arm, filter.symbol, filter.era, from, to],
    queryFn: async () => {
      const range = new URLSearchParams();
      if (from !== null) range.set("from", from);
      if (to !== null) range.set("to", to);
      const res = await fetch(`/api/flies/history?${fliesQuery(mode, { ...filter, date: null })}&${range.toString()}`);
      if (!res.ok) throw new Error(`history: HTTP ${res.status}`);
      return (await res.json()) as History;
    },
    refetchInterval: 60_000,
  });
}

function SummaryRows<T extends Summary>({
  rows,
  label,
  thinBelow,
}: {
  rows: T[] | undefined;
  label: keyof T;
  /** The regime-cuts writer's declared floor; null when it has published none. */
  thinBelow: number | null;
}) {
  return (
    <>
      {rows?.map((r) => {
        // Sessions are the unit of independence, so they decide whether the rest of the row means
        // anything. Below the writer's floor the net, win rate and profit factor are a day or two of
        // weather; dimmed rather than hidden, because the row is still the honest record of what
        // happened. The floor is the one core.regimecuts declares, read from its artifact -- not a 3
        // kept here, which is how two surfaces start disagreeing about the same row.
        const thin = thinBelow !== null && r.sessions < thinBelow;
        return (
          <tr key={String(r[label])} style={thin ? { opacity: 0.55 } : undefined}>
            <td>
              {String(r[label])}
              {thin && (
                <span className="muted" style={{ fontSize: 10, marginLeft: 4 }} title={`Fewer than ${thinBelow} sessions — same-day trades share a regime, so this is a handful of independent observations however many trades it holds.`}>
                  thin
                </span>
              )}
            </td>
            <td>{r.trades}</td>
            <td className={thin ? "muted" : undefined}>{r.sessions}</td>
            <td><PnlCell v={r.netPnl} /></td>
            <td>{r.winRatePct !== null ? `${r.winRatePct.toFixed(0)}%` : "—"}</td>
            <td>{r.avgPnl !== null ? fmtMoney(r.avgPnl) : "—"}</td>
            <td>{r.profitFactor !== null ? r.profitFactor.toFixed(2) : "—"}</td>
          </tr>
        );
      })}
    </>
  );
}

/** The ranked summaries' headers. The entry-hour card keeps plain ones: its order is the clock. */
const SUMMARY_HEADERS: Array<string | SortHeader> = [
  "",
  { label: "trades", k: "trades" },
  { label: "sessions", k: "sessions" },
  { label: "net", k: "net" },
  { label: "win %", k: "win" },
  { label: "avg", k: "avg" },
  { label: "PF", k: "pf" },
];

const SUMMARY_SORTS = {
  trades: (r: Summary) => r.trades,
  sessions: (r: Summary) => r.sessions,
  net: (r: Summary) => r.netPnl,
  win: (r: Summary) => r.winRatePct,
  avg: (r: Summary) => r.avgPnl,
  pf: (r: Summary) => r.profitFactor,
};

const FEE_DRAG_HEADERS: SortHeader[] = [
  { label: "arm", k: "arm" },
  { label: "premium", k: "premium" },
  { label: "gross", k: "gross" },
  { label: "fees", k: "fees" },
  { label: "settle", k: "settle" },
  { label: "net", k: "net" },
  { label: "fee drag", k: "drag" },
];

const FEE_DRAG_SORTS = {
  arm: (r: { arm: string } & Summary) => r.arm,
  premium: (r: Summary) => r.premium,
  gross: (r: Summary) => r.grossPnl,
  fees: (r: Summary) => r.fees,
  settle: (r: Summary) => r.settlementFees,
  net: (r: Summary) => r.netPnl,
  drag: (r: Summary) => r.feeDragPct,
};

/** Daily P&L calendar, Monday at the top of each week column; click a day to replay it. */
export function FliesCalendar({
  days,
  onPick,
}: {
  days: Array<{ date: string; trades: number; netPnl: number }>;
  onPick?: (date: string) => void;
}) {
  if (days.length === 0) return <p className="muted">no settled days yet</p>;
  const maxAbs = Math.max(...days.map((d) => Math.abs(d.netPnl)), 1);
  const weeks = new Map<string, Array<{ date: string; trades: number; netPnl: number; weekday: number }>>();
  for (const d of days) {
    const dt = new Date(d.date + "T00:00:00Z");
    const weekday = (dt.getUTCDay() + 6) % 7;
    const monday = new Date(dt);
    monday.setUTCDate(dt.getUTCDate() - weekday);
    const key = monday.toISOString().slice(0, 10);
    let col = weeks.get(key);
    if (col === undefined) {
      col = [];
      weeks.set(key, col);
    }
    col.push({ ...d, weekday });
  }
  return (
    <div style={{ display: "flex", gap: 4, overflowX: "auto", paddingBottom: 4 }}>
      {[...weeks.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([week, cells]) => (
        <div key={week} style={{ display: "grid", gridTemplateRows: "repeat(5, 18px)", gap: 4 }}>
          {[0, 1, 2, 3, 4].map((wd) => {
            const cell = cells.find((c) => c.weekday === wd);
            if (cell === undefined)
              return <div key={wd} style={{ width: 18, height: 18, background: "var(--track)", borderRadius: 3 }} />;
            const alpha = 0.2 + 0.8 * (Math.abs(cell.netPnl) / maxAbs);
            const color = `color-mix(in srgb, var(${cell.netPnl >= 0 ? "--pos" : "--neg"}) ${Math.round(alpha * 100)}%, transparent)`;
            return (
              <div
                key={wd}
                role={onPick !== undefined ? "button" : undefined}
                title={`${cell.date}: ${fmtMoney(cell.netPnl)} (${cell.trades} positions)${onPick !== undefined ? " — click to replay" : ""}`}
                onClick={() => onPick?.(cell.date)}
                style={{ width: 18, height: 18, background: color, borderRadius: 3, cursor: onPick !== undefined ? "pointer" : "default" }}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}

const OUTCOMES = ["all", "wins", "losses", "pinned", "risk-free"] as const;

const UNRECORDED_SLIP =
  "not recorded: flies records slippage from 2026-09-25 on: what a paper fill's model concedes, and on a live fill what the entry conceded against the mid it was asked from";

/**
 * The trade log's columns, declared once (see components/table/columns.ts): describe columns can be
 * hidden and reordered, the money block keeps the standard's order, and net is always last.
 */
const TRADE_LOG_COLUMNS: ColumnDef<FliesTradeLogRow>[] = [
  { id: "date", header: "date", kind: "describe", render: (r) => r.tradeDate },
  { id: "time", header: "time", kind: "describe", className: "muted", render: (r) => clockTime(r.entryTime) },
  { id: "sym", header: "sym", kind: "describe", render: (r) => r.symbol },
  { id: "arm", header: "arm", kind: "describe", className: "muted", render: (r) => r.arm ?? "—" },
  { id: "mode", header: "mode", kind: "describe", className: "muted", render: (r) => r.entryMode ?? "—" },
  { id: "kind", header: "kind", kind: "describe", render: (r) => structureLabel(r.kind, r.side) },
  { id: "centre", header: "centre", kind: "describe", render: (r) => fmtNum(r.center, 0) },
  {
    id: "wing",
    header: "wing",
    title: "wing width in points; near/far when the wing is broken",
    kind: "describe",
    render: (r) => wingWidth(r.wingWidth, r.farWidth),
  },
  { id: "window", header: "window", kind: "describe", className: "muted", render: (r) => r.window ?? "—" },
  { id: "qty", header: "qty", kind: "describe", render: (r) => r.quantity ?? "—" },
  {
    id: "price",
    header: "price",
    title: "net entry price per share — cr received, db paid",
    kind: "describe",
    render: (r) => fmtPrice(r.price),
  },
  { id: "how", header: "how", kind: "describe", className: "muted", render: (r) => r.exitKind ?? "—" },
  {
    id: "latency",
    header: "latency",
    title: "minutes from entry to the completing fill",
    kind: "describe",
    className: "muted",
    render: (r) => (r.latencyMin !== null ? `${r.latencyMin.toFixed(0)}m` : "—"),
  },
  {
    id: "pinned",
    header: "pinned",
    title: "settled inside the short strike's band",
    kind: "describe",
    render: (r) => (r.pinned ? <span className="chain-badge chain-badge-short">pinned</span> : null),
  },
  {
    id: "entry",
    header: "entry",
    title: "entry cash flow for the whole position: + received, − paid",
    kind: "money",
    render: (r) => fmtCash(r.entryCash),
  },
  {
    id: "exit",
    header: "exit",
    title: "exit cash flow: the close, or the settlement payoff at the bell",
    kind: "money",
    render: (r) => fmtCash(r.exitCash),
  },
  { id: "gross", header: "gross", title: "entry + exit, before any cost", kind: "money", render: (r) => fmtMoney(r.gross) },
  {
    id: "fees",
    header: "fees",
    title: "trading fees: commissions and exchange fees",
    kind: "money",
    className: "muted",
    render: (r) => (
      <span title={r.settlementFees === null ? "the fee total: its settlement share was not recorded" : undefined}>
        {fmtMoney(r.fees)}
      </span>
    ),
  },
  {
    id: "settle",
    header: "settle",
    title: "exercise / assignment fees at settlement",
    kind: "money",
    className: "muted",
    render: (r) => (r.settlementFees === null ? "n/r" : fmtMoney(r.settlementFees)),
  },
  {
    id: "slip",
    header: "slip",
    title: "what the fills gave up against mid — already inside gross, never subtracted again",
    kind: "money",
    className: "muted",
    render: (r) => (
      <span title={r.slippage === null ? UNRECORDED_SLIP : "conceded against mid — inside gross, never subtracted"}>
        {r.slippage === null ? "n/r" : fmtMoney(r.slippage)}
      </span>
    ),
  },
  { id: "net", header: "net", title: "gross − fees − settlement", kind: "money", pinned: true, render: (r) => <PnlCell v={r.pnl} /> },
];

/**
 * The column ids the server can order the WHOLE trade log by (`TRADE_LOG_SORTS` in
 * server/src/readers/flies.ts; a server test holds the two lists together). The log is paged on
 * the server, so a sort of the fetched page alone would rank one page and call it the history.
 */
const TRADE_LOG_SORTS: readonly string[] = [
  "date", "time", "sym", "arm", "mode", "centre", "qty", "price", "latency",
  "entry", "exit", "gross", "fees", "settle", "slip", "net",
];

/* ---------------------------------------------------------------- help */

export const CALENDAR_HELP: CardHelpContent = {
  title: "Calendar",
  body: (
    <HelpSection title="Settled days">
      <p>One square a settled day, in a column a week, Monday at the top; its depth is the day's net against the largest in view. Open a day to replay it on the session tab.</p>
    </HelpSection>
  ),
};

export const BY_ARM_HELP: CardHelpContent = {
  title: "By arm",
  body: (
    <>
      <HelpWarn title="Thin rows">
        <p>
          A row with fewer sessions than the regime-cuts writer's floor is dimmed and tagged thin: same-day trades share a regime, so it is
          a handful of independent observations however many trades it holds. The floor is the one core.regimecuts declares, read from its
          artifact. A dimmed row is still the honest record of what happened.
        </p>
      </HelpWarn>
      <HelpSection title="What is counted">
        <p>Legged entries only, over settled days. Sessions sit beside trades everywhere, because the session is the unit of independence.</p>
      </HelpSection>
    </>
  ),
};

export const TRADE_LOG_HELP: CardHelpContent = {
  title: "The trade log",
  body: (
    <>
      <HelpSection title="Totals">
        <p>
          The line under the head sums every row matching these filters, not just this page: gross less fees and settlement is net. Slippage
          is what the modelled fills conceded against mid: already inside gross, never subtracted again, and recorded on the trades it says.
          Sessions ride beside the net because same-day trades share a regime.
        </p>
      </HelpSection>
      <HelpSection title="A trade">
        <p>
          One entry, closed or settled: entry and exit cash signed (received +, paid -), adding to gross; fees, settlement and slippage each
          in their own column. n/r means the figure was not recorded. Rows read as a table or as cards; a row opens its card in place.
        </p>
      </HelpSection>
    </>
  ),
};

/* ---------------------------------------------------------------- the trade log */

export function logModel(r: FliesTradeLogRow): TradeCardModel {
  const wing = wingWidth(r.wingWidth, r.farWidth);
  const slip = r.slippage === null ? "n/r" : fmtMoney(r.slippage);
  return {
    key: `${r.tradeDate}-${r.entryTime ?? ""}-${r.arm ?? ""}-${r.kind ?? ""}-${r.center ?? ""}-${r.side ?? ""}-${r.price ?? ""}`,
    symbol: r.symbol,
    symbolHref: `/charts/technicals?symbol=${encodeURIComponent(r.symbol)}`,
    spot: null,
    shape: `${structureLabel(r.kind, r.side)} · ${fmtNum(r.center, 0)}${wing === "—" ? "" : ` · ${wing} wide`}`,
    chips: (
      <>
        {r.arm ? <span className="chip">{r.arm}</span> : null}
        {r.exitKind ? <span className="chip">{r.exitKind}</span> : null}
        {r.pinned ? <span className="chip chip-warn">pinned</span> : null}
      </>
    ),
    cash: r.pnl,
    side: [`net · ${fmtPrice(r.price)}`, r.quantity === null ? "qty —" : `qty ${r.quantity}`],
    bar: null,
    barTitle: "",
    label: `entry ${fmtCash(r.entryCash)} · exit ${fmtCash(r.exitCash)} · gross ${fmtMoney(r.gross)}`,
    open: { date: `${r.tradeDate.slice(5)} ${clockTime(r.entryTime)}`, price: fmtPrice(r.price) },
    legs: [],
    after: [
      `fees ${fmtMoney(r.fees)}`,
      `settlement ${r.settlementFees === null ? "n/r" : fmtMoney(r.settlementFees)}`,
      `slippage ${slip}`,
    ],
    foot: [`entry mode: ${r.entryMode ?? "—"}`, r.latencyMin !== null ? `completed in ${r.latencyMin.toFixed(0)}m` : (r.window ?? "—")],
  };
}

/** The log's panel sorts. The server orders the whole result; a column header sorts by any other. */
export const LOG_SORTS: SortOption<FliesTradeLogRow>[] = [
  { id: "date", label: "date", hint: "newest first", key: () => null },
  { id: "net", label: "net", hint: "worst first", key: () => null },
];
const LOG_DIR: Record<string, "asc" | "desc"> = { date: "desc", net: "asc" };

export function logChoice(sort: string | undefined, dir: "asc" | "desc" | undefined): SortChoice {
  if (sort === undefined) return { id: "date", reversed: false };
  const base = LOG_DIR[sort];
  return base === undefined ? { id: sort, reversed: false } : { id: sort, reversed: (dir ?? "desc") !== base };
}

function TradeLogCard({
  view,
  setView,
  cols,
  log,
  logQuery,
  logTotal,
  totals,
  outcome,
  setOutcome,
  search,
  setSearch,
  page,
  setOffset,
  setLimit,
  toggleSort,
  setSort,
}: {
  view: ListView;
  setView: (v: ListView) => void;
  cols: ReturnType<typeof useColumnLayout<FliesTradeLogRow>>;
  log: FliesTradeLogRow[];
  logQuery: ReturnType<typeof useFliesTradeLog>;
  logTotal: number;
  totals: ReturnType<typeof useFliesTradeLog>["data"] extends infer D ? (D extends { totals: infer T } ? T : never) | undefined : never;
  outcome: (typeof OUTCOMES)[number];
  setOutcome: (o: (typeof OUTCOMES)[number]) => void;
  search: string;
  setSearch: (v: string) => void;
  page: { offset: number; limit: number; sort?: string; dir?: "asc" | "desc" };
  setOffset: (v: number) => void;
  setLimit: (v: number) => void;
  toggleSort: (k: string) => void;
  setSort: (k?: string, d?: "asc" | "desc") => void;
}) {
  const [panel, setPanel] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const anchor = useRef<HTMLButtonElement>(null);
  const choice = logChoice(page.sort, page.dir);
  const onLogSort = LOG_SORTS.find((o) => o.id === choice.id);
  const changed = (outcome !== "all" ? 1 : 0) + (search !== "" ? 1 : 0) + (page.sort !== undefined ? 1 : 0) + (cols.isDefault ? 0 : 1);
  const reset = () => {
    setOutcome("all");
    setSearch("");
    setSort();
    cols.reset();
  };
  const sortWords = onLogSort ? `by ${onLogSort.label}${choice.reversed ? ", reversed" : `, ${onLogSort.hint}`}` : `by ${choice.id}`;
  // The totals line is the card's own state line, over every matching row: what it leaves out stays on it.
  const totalsNote =
    totals !== undefined && totals.trades > 0
      ? `net ${fmtMoney(totals.netPnl)} · ${totals.trades.toLocaleString()} trades · ${totals.sessions.toLocaleString()} session${totals.sessions === 1 ? "" : "s"} · gross ${fmtMoney(totals.grossPnl)} · fees ${fmtMoney(totals.fees)} · settlement ${fmtMoney(totals.settlementFees)} · slippage ${fmtMoney(totals.slippage)}${totals.slippageTrades === totals.trades ? "" : ` (recorded on ${totals.slippageTrades.toLocaleString()} of ${totals.trades.toLocaleString()})`}`
      : null;
  const toggle = (k: string) => {
    const next = new Set(open);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    setOpen(next);
  };
  const models = log.map(logModel);
  return (
    <>
      <Card
        title={`trade log — ${logTotal.toLocaleString()} matching`}
        help={TRADE_LOG_HELP}
        controls={
          <>
            <ViewSwitch view={view} onChange={setView} />
            <FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} />
          </>
        }
      >
        <FilterSummary
          parts={[sortWords, ...(outcome === "all" ? [] : [outcome]), ...(search === "" ? [] : [`“${search}”`])]}
          note={totalsNote}
          changed={changed}
          onOpen={() => setPanel(true)}
          onReset={reset}
        />
        {view === "cards" ? (
          <div className={logQuery.isPlaceholderData ? "table-busy" : undefined}>
            <ListViewContext.Provider value="cards">
              <TradeCards models={models} empty="no trades match these filters" />
            </ListViewContext.Provider>
          </div>
        ) : (
          <div className={`table-scroll ${logQuery.isPlaceholderData ? "table-busy" : ""}`}>
            <table className="data-table">
              <thead>
                <tr>
                  <th aria-label="open" />
                  {cols.columns.map((c) => {
                    if (!TRADE_LOG_SORTS.includes(c.id)) {
                      return (
                        <th key={c.id} title={c.title}>
                          {c.header}
                        </th>
                      );
                    }
                    // Sorted on the server, over every matching row; a click goes back to page one.
                    const on = page.sort === c.id;
                    return (
                      <th key={c.id} title={c.title} aria-sort={on ? (page.dir === "asc" ? "ascending" : "descending") : undefined}>
                        <button type="button" className="th-sort" onClick={() => toggleSort(c.id)}>
                          {c.header}
                          {on ? (page.dir === "asc" ? " ▴" : " ▾") : ""}
                        </button>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {log.map((r, i) => {
                  const m = models[i]!;
                  const isOpen = open.has(m.key);
                  return (
                    <Fragment key={`${m.key}-${i}`}>
                      <tr>
                        <td>
                          <button
                            type="button"
                            className={`btn btn-quiet collapse-toggle ${isOpen ? "collapse-toggle-open" : ""}`}
                            aria-expanded={isOpen}
                            aria-label={`${isOpen ? "Close" : "Open"} ${m.shape}`}
                            onClick={() => toggle(m.key)}
                          >
                            ▸
                          </button>
                        </td>
                        {cols.columns.map((c) => (
                          <td key={c.id} className={c.className}>
                            {c.render(r)}
                          </td>
                        ))}
                      </tr>
                      {isOpen && (
                        <tr className="fl-open">
                          <td colSpan={cols.columns.length + 1}>
                            <TradeCard m={m} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {logTotal > 0 && (
          <div className="card-footer">
            <Pager offset={logQuery.data?.offset ?? page.offset} limit={logQuery.data?.limit ?? page.limit} total={logTotal} onOffset={setOffset} onLimit={setLimit} />
          </div>
        )}
      </Card>
      <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
        <SortSection
          sorts={LOG_SORTS}
          choice={choice}
          fallback="date"
          onChange={(c) => setSort(c.id === "date" && !c.reversed ? undefined : c.id, (LOG_DIR[c.id] === "desc") !== c.reversed ? "desc" : "asc")}
        />
        <FilterGroup title="outcome">
          <ChoiceGroup label="outcome filter" value={outcome} options={OUTCOMES.map((o) => ({ id: o, label: o }))} onChange={setOutcome} />
        </FilterGroup>
        <FilterGroup title="columns" note="the table's; cards ignore them">
          <ColumnsMenu defs={TRADE_LOG_COLUMNS} layout={cols.layout} onChange={cols.setLayout} onReset={cols.reset} isDefault={cols.isDefault} />
        </FilterGroup>
        <FilterGroup title="search" note="symbol, arm, mode">
          <input className="text-input" placeholder="search…" aria-label="search the trade log" value={search} onChange={(e) => setSearch(e.target.value)} style={{ textTransform: "none" }} />
        </FilterGroup>
      </FilterSortPanel>
    </>
  );
}

/* ---------------------------------------------------------------- across sessions */

const ACROSS = [
  { id: "mode", label: "entry mode" },
  { id: "hour", label: "entry hour" },
  { id: "fee", label: "fee drag" },
  { id: "completion", label: "completion by hour" },
] as const;

export function HistoryTab({
  mode,
  filter,
  onReplayDay,
}: {
  mode: TradingMode;
  filter: FliesFilter;
  onReplayDay: (date: string) => void;
}) {
  // `date` is dropped on purpose: this tab answers questions ACROSS sessions, so pinning the day
  // selected on the Today tab would empty it.
  // The date range is in the page address and bounds the WHOLE tab (2026-09-25): the summaries
  // and the trade log alike, so a narrowed page never sets one table beside cards still answering
  // for the era. With no range set, everything reads the era, as it always has.
  const { from, to } = useUrlDateRange();
  const { data, isLoading } = useHistory(mode, filter, from, to);
  const [outcome, setOutcome] = useState<(typeof OUTCOMES)[number]>("all");
  const [search, setSearch] = useState("");
  const [view, setView] = useViewChoice("flies-tradelog", "rows");
  const box = useOpenParam("fl");
  const [acrossTab, setAcrossTab] = useState<(typeof ACROSS)[number]["id"]>("mode");

  // Search reaches the DB now, so let typing settle first.
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(t);
  }, [search]);

  // Explicit date bounds, either side independently empty. In the page address since 2026-09-25, so
  // a filtered log survives a reload and can be shared.
  const range = { from, to };
  const cols = useColumnLayout("flies-history", TRADE_LOG_COLUMNS);

  const { page, setOffset, setLimit, toggleSort, setSort } = usePage([mode, outcome, debouncedSearch, from, to, filter.arm, filter.era]);
  const armSort = useSort();
  const modeSort = useSort();
  const feeSort = useSort();
  // `filter.arm` and `filter.era` are the PAGE-level scope, the same one every other card on this
  // tab already honours. The log took era and ignored arm, so narrowing to one arm left it
  // answering for all of them beside tables that did not.
  const logQuery = useFliesTradeLog(mode, outcome, debouncedSearch, page, filter.era, range, filter.arm);
  const log = logQuery.data?.rows ?? [];
  const logTotal = logQuery.data?.total ?? 0;
  const totals = logQuery.data?.totals;

  // Sessions beside trades, everywhere. Same-day trades share a regime and are not independent
  // observations: this module's own experiment docs put the effective N at the day count.
  const headers = ["", "trades", "sessions", "net", "win %", "avg", "PF"];

  // Arm x hour completion matrix: rows by arm, columns by the hours that actually appear, sorted
  // chronologically. A Map per arm keeps a missing (arm, hour) combination a real "no trades" dash
  // rather than a zero, matching this module's own "None never means zero" rule.
  const hourColumns = [...new Set((data?.byArmHour ?? []).map((r) => r.hour))].sort((a, b) => a.localeCompare(b));
  const armRows: Array<[string, Map<string, CompletionSummary>]> = [];
  {
    const byArmMap = new Map<string, Map<string, CompletionSummary>>();
    for (const r of data?.byArmHour ?? []) {
      let hourMap = byArmMap.get(r.arm);
      if (hourMap === undefined) {
        hourMap = new Map<string, CompletionSummary>();
        byArmMap.set(r.arm, hourMap);
      }
      hourMap.set(r.hour, r);
    }
    for (const [arm, hourMap] of byArmMap) armRows.push([arm, hourMap]);
  }

  const thin = data?.thinBelowSessions ?? null;
  const acrossBody = (tab: (typeof ACROSS)[number]["id"]) => {
    if (tab === "mode")
      return (
        <DataCard title="By entry mode" headers={SUMMARY_HEADERS} sort={modeSort} loading={isLoading} rowCount={data?.byEntryMode.length ?? 0}>
          <SummaryRows rows={sortRows(data?.byEntryMode ?? [], modeSort, SUMMARY_SORTS)} label="entryMode" thinBelow={thin} />
        </DataCard>
      );
    if (tab === "hour")
      return (
        <>
          <p className="muted" style={{ margin: "0 0 0.5rem" }}>Deliberately unranked: the order is the clock.</p>
          <DataCard title="By entry hour" headers={headers} loading={isLoading} rowCount={data?.byEntryHour.length ?? 0}>
            <SummaryRows rows={data?.byEntryHour} label="hour" thinBelow={thin} />
          </DataCard>
        </>
      );
    if (tab === "fee")
      return (
        <DataCard title="Fee drag by arm" headers={FEE_DRAG_HEADERS} sort={feeSort} loading={isLoading} rowCount={data?.feeDrag.length ?? 0}>
          {sortRows(data?.feeDrag ?? [], feeSort, FEE_DRAG_SORTS).map((r) => (
            <tr key={r.arm}>
              <td>{r.arm}</td>
              <td title="premium collected: each credit fly's entry credit, whole position">{fmtMoney(r.premium)}</td>
              <td>{fmtMoney(r.grossPnl)}</td>
              <td className="pnl-neg" title={r.settlementFees === null ? "trading fees, with settlement left in on the trades that never recorded the split" : "trading fees: commissions and exchange fees"}>
                {fmtMoney(r.fees)}
              </td>
              <td className="muted">{r.settlementFees === null ? "n/r" : fmtMoney(r.settlementFees)}</td>
              <td><PnlCell v={r.netPnl} /></td>
              <td className="muted">{r.feeDragPct !== null ? `${r.feeDragPct.toFixed(1)}%` : "—"}</td>
            </tr>
          ))}
        </DataCard>
      );
    return (
      <>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th></th>
                {hourColumns.map((h) => (
                  <th key={h}>{shortHour(h)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <SkeletonRows n={3} cols={hourColumns.length + 1} />
              ) : armRows.length === 0 ? (
                <tr>
                  <td colSpan={hourColumns.length + 1} className="muted">no rows</td>
                </tr>
              ) : (
                armRows.map(([arm, byHour]) => (
                  <tr key={arm}>
                    <td>{arm}</td>
                    {hourColumns.map((h) => {
                      const c = byHour.get(h);
                      if (c === undefined || c.trades === 0) return <td key={h} className="muted">—</td>;
                      const cls = c.completionPct === null ? undefined : c.completionPct >= 70 ? "pnl-pos" : c.completionPct < 55 ? "pnl-neg" : undefined;
                      return (
                        <td key={h} className={cls} title={`${c.trades} trades, ${c.completed} completed`}>
                          {c.completionPct !== null ? `${c.completionPct.toFixed(0)}%` : "—"}
                          {" · "}
                          {c.medianLatencyMin !== null ? `${c.medianLatencyMin.toFixed(0)}m` : "—"}
                          {" · "}
                          {c.pinnedPct !== null ? `${c.pinnedPct.toFixed(0)}%` : "—"}
                        </td>
                      );
                    })}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <p className="muted" style={{ fontSize: 11, marginTop: "0.5rem", marginBottom: 0 }}>
          Each cell reads completion % · median minutes to complete · pinned %. Completion is whether the second leg ever filled; pinned is
          a settlement inside the short strike's band. A dash means no trades entered that arm in that hour.
        </p>
      </>
    );
  };

  return (
    <FliesLayout>
      <div className="history-controls" style={{ margin: 0 }}>
        <DateRangeBar basis="trade date" allTitle="every session in the era picked in the header" />
      </div>
      <FliesSection concept="hist" name="calendar" question="How did each day go?">
        <Card title="daily P&L calendar" className="fl-w6" help={CALENDAR_HELP}>
          {isLoading ? <span className="skeleton skeleton-text" style={{ width: "40%" }} /> : <FliesCalendar days={data?.dailyPnl ?? []} onPick={onReplayDay} />}
        </Card>
        <Card title="by arm" className="fl-w6" help={BY_ARM_HELP} flag={thin !== null ? "thin rows dimmed" : undefined}>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  {SUMMARY_HEADERS.map((h, i) =>
                    typeof h === "string" ? (
                      <th key={i}>{h}</th>
                    ) : (
                      <SortTh key={h.k} label={h.label} k={h.k} sort={armSort} title={h.title} />
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {isLoading ? <SkeletonRows n={4} cols={7} /> : <SummaryRows rows={sortRows(data?.byArm ?? [], armSort, SUMMARY_SORTS)} label="arm" thinBelow={thin} />}
              </tbody>
            </table>
          </div>
          <div className="pf-mores pf-push">
            <button type="button" className="pf-more" onClick={() => box.show("across")}>
              by entry mode, hour, fee drag <span aria-hidden="true">›</span>
            </button>
          </div>
        </Card>
      </FliesSection>

      <FliesSection concept="hist" name="trade log" question="Every trade">
        <TradeLogCard
          view={view}
          setView={setView}
          cols={cols}
          log={log}
          logQuery={logQuery}
          logTotal={logTotal}
          totals={totals}
          outcome={outcome}
          setOutcome={setOutcome}
          search={search}
          setSearch={setSearch}
          page={page}
          setOffset={setOffset}
          setLimit={setLimit}
          toggleSort={toggleSort}
          setSort={setSort}
        />
      </FliesSection>

      {box.open === "across" ? (
        <CardLightbox count={1} index={0} onStep={() => undefined} onClose={box.close} wide>
          {(nav) => (
            <LightboxFrame
              kicker="history ›"
              title="Across sessions"
              tabs={ACROSS.map((a) => ({ id: a.id, label: a.label }))}
              tab={acrossTab}
              onTab={setAcrossTab}
              position={nav.position}
              onPrev={nav.prev}
              onNext={nav.next}
              onClose={box.close}
            >
              <div className="pf-box-body">{acrossBody(acrossTab)}</div>
            </LightboxFrame>
          )}
        </CardLightbox>
      ) : null}
    </FliesLayout>
  );
}
