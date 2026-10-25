import path from "node:path";
import type { EarningsTradeRow, ModuleSummarySlice, SummaryGauge, SummaryTile } from "@console/shared";
import type { ConsoleConfig } from "../../config.js";
import { readRetiredArms } from "../armStatus.js";
import { num, readOnlyDb, suiteEra } from "../db.js";
import { readEarnings, EARNINGS_FIRST_PAGE } from "../earnings.js";
import { MAX_PAGE } from "../paging.js";
import { sessionDate } from "../../services/report.js";
import {
  armsOf,
  breakdownOf,
  emptySlice,
  heroOf,
  worstNet,
  inWindow,
  scopeTrades,
  sessionNeighbours,
  summaryWindow,
  type SummaryQuery,
  type SummaryTrade,
} from "./common.js";

const UNIT = "one earnings structure on one name";
const SYMBOL_ROWS = 12;

const round2 = (v: number) => Math.round(v * 100) / 100;

/** A settled history row, carried with what the breakdowns and tiles read beside its money. */
interface EarningsSummaryTrade extends SummaryTrade {
  orderId: string;
  symbol: string;
  strategy: string;
  exitReason: string | null;
}

/** The per-row facts the history reader does not carry: max loss and sessions held. */
interface RowExtras {
  capitalAtRisk: number | null;
  holdDays: number | null;
}

/**
 * Every history row the earnings page would show for the suite era, both books. Read through
 * `readEarnings` itself, so the money is that reader's arithmetic and nothing else; paged at its
 * ceiling, which one call covers until the books pass 500 closed rows.
 */
function historyRows(config: ConsoleConfig): EarningsTradeRow[] {
  const rows: EarningsTradeRow[] = [];
  for (let offset = 0; ; offset += MAX_PAGE) {
    // era null = the history page's default scope (the suite's data epoch).
    const page = readEarnings(config, { ...EARNINGS_FIRST_PAGE, trades: { limit: MAX_PAGE, offset } }, null).trades;
    rows.push(...page.rows);
    if (page.rows.length === 0 || offset + page.rows.length >= page.total) break;
  }
  return rows;
}

/**
 * Earnings' simplified-view summary. A trade is one defined-risk structure (iron fly, condor,
 * calendar, ...) on one name around its report, booked to the session it CLOSED in -- the date the
 * earnings history filters on. Money is the earnings history reader's own rows (`readEarnings`):
 * gross is `pnl`; fees, settlement and the CHARGED slippage come out of entry_cost + exit_cost once
 * each, so the hero equals that page's totals for the same rows.
 *
 * Earnings is two-axis: the ARM (stored `arm`, once `profile`) is the book a row belongs to --
 * `strat_test:<strategy>` or an advised twin -- while `strategy` is the structure type. The by-arm
 * card is the arm; the structure table is the strategy. Earnings has a live ledger, so `q.mode` is
 * honoured.
 */
export function readEarningsSummary(config: ConsoleConfig, q: SummaryQuery): ModuleSummarySlice {
  const mode = q.mode;
  const dbPath = path.join(config.paths.earningsDir, mode === "live" ? "earnings_trades.db" : "paper_trades.db");

  // One pass over the book for the per-row facts the history rows lack, and the open positions.
  // A missing or unreadable ledger is the empty slice, never a throw.
  const extras = readOnlyDb(dbPath, (db) => {
    const cols = new Set(
      db
        .prepare<[], Record<string, unknown>>("PRAGMA table_info(trades)")
        .all()
        .map((c) => String(c["name"])),
    );
    if (cols.size === 0) throw new Error("no trades table");
    const col = (name: string) => (cols.has(name) ? name : "NULL");
    const rows = db
      .prepare<[], Record<string, unknown>>(
        `SELECT order_id, opened_at, closed_at, ${col("capital_at_risk")} AS capital_at_risk,
                ${col("hold_days")} AS hold_days
           FROM trades`,
      )
      .all();
    const byOrder = new Map<string, RowExtras>();
    const openedSessions: Array<string | null> = [];
    for (const r of rows) {
      byOrder.set(String(r["order_id"] ?? ""), { capitalAtRisk: num(r["capital_at_risk"]), holdDays: num(r["hold_days"]) });
      if (r["closed_at"] == null) openedSessions.push(sessionDate(r["opened_at"]));
    }
    return { byOrder, openedSessions };
  });
  if (extras.status !== "ok") {
    const slice = emptySlice("earnings", mode, UNIT, summaryWindow(q.range, q.day));
    slice.notes.push(`No ${mode} earnings ledger could be read, so there is nothing to summarise.`);
    return slice;
  }

  const settled: EarningsSummaryTrade[] = historyRows(config)
    .filter((r) => r.mode === mode && r.closedAt !== null && r.gross !== null && r.net !== null)
    .flatMap((r) => {
      const session = sessionDate(r.closedAt);
      if (session === null) return [];
      return [
        {
          session,
          arm: r.arm ?? "default",
          gross: r.gross!,
          fees: r.fees,
          settlement: r.settlementFees,
          slippage: r.slippage,
          net: r.net!,
          orderId: r.orderId,
          symbol: r.symbol,
          strategy: r.strategy,
          exitReason: r.exitReason,
        },
      ];
    });

  const session = sessionNeighbours(
    settled.map((t) => t.session),
    q.day,
  );
  const window = summaryWindow(q.range, session.day);
  const trades = scopeTrades(settled.filter((t) => inWindow(t.session, window)), q);
  // Open now and already open by the window's end; none of their money is in the hero.
  const open = extras.value.openedSessions.filter((s) => window.to === null || s === null || s <= window.to).length;
  const hero = heroOf(trades, true, open, (t) => extras.value.byOrder.get(t.orderId)?.capitalAtRisk);

  // --- tiles ---
  const nets = trades.map((t) => t.net);
  const won = nets.filter((n) => n > 0).reduce((s, n) => s + n, 0);
  const lost = Math.abs(nets.filter((n) => n < 0).reduce((s, n) => s + n, 0));
  const extra = (t: EarningsSummaryTrade) => extras.value.byOrder.get(t.orderId);
  const held = trades.map((t) => extra(t)?.holdDays ?? null).filter((v): v is number => v !== null);
  const names = new Set(trades.map((t) => t.symbol)).size;
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const tiles: SummaryTile[] = [
    {
      label: "average net per trade",
      value: trades.length > 0 ? round2(hero.net / trades.length) : null,
      unit: "usd",
      note: `net ÷ ${plural(trades.length, "settled trade")}`,
    },
    {
      label: "profit factor",
      value: lost > 0 ? round2(won / lost) : null,
      unit: "ratio",
      note: lost > 0 ? "net won ÷ net lost" : "n/r: no losing trade in the window",
      signed: false,
    },
    {
      label: "average sessions held",
      value: held.length > 0 ? Math.round((held.reduce((s, v) => s + v, 0) / held.length) * 10) / 10 : null,
      unit: "days",
      note: `trading sessions, 1 = the overnight hold; ${held.length}/${trades.length} trades recorded it`,
      signed: false,
    },
    {
      label: "names traded",
      value: names,
      unit: "count",
      note: `distinct underlyings across ${plural(trades.length, "trade")}`,
      signed: false,
    },
  ];

  // --- gauge: return on the capital the module put at risk (its own sizing's max loss) ---
  const atRisk = trades.filter((t) => (extra(t)?.capitalAtRisk ?? 0) > 0);
  const capital = atRisk.reduce((s, t) => s + (extra(t)?.capitalAtRisk ?? 0), 0);
  const riskNet = atRisk.reduce((s, t) => s + t.net, 0);
  const gauges: SummaryGauge[] = [
    {
      title: "return on capital at risk",
      rawPct: capital > 0 ? Math.round((riskNet / capital) * 1000) / 10 : null,
      min: -100,
      max: 100,
      caption:
        capital > 0
          ? `net ÷ summed max loss ($${Math.round(capital).toLocaleString("en-US")}); ${atRisk.length}/${trades.length} trades recorded a max loss`
          : "n/r: no trade in the window recorded its max loss",
      dollars:
        capital > 0
          ? {
              amount: Math.round(riskNet * 100) / 100,
              of: Math.round(capital * 100) / 100,
              ofLabel: "capital at risk",
              worst: worstNet(atRisk),
            }
          : null,
    },
  ];

  // --- breakdowns ---
  const byStrategy = breakdownOf("by structure", "strategy", trades, (t) => t.strategy || "n/r");
  byStrategy.rows.sort((a, b) => b.net - a.net);
  const byExit = breakdownOf("by exit", "exit reason", trades, (t) => t.exitReason ?? "n/r");
  byExit.rows.sort((a, b) => b.trades - a.trades);
  const bySymbol = breakdownOf("by symbol", "symbol", trades, (t) => t.symbol || "n/r");
  const symbolCount = bySymbol.rows.length;
  bySymbol.rows = bySymbol.rows.sort((a, b) => Math.abs(b.net) - Math.abs(a.net)).slice(0, SYMBOL_ROWS);
  if (symbolCount > SYMBOL_ROWS) bySymbol.title = `by symbol (top ${SYMBOL_ROWS} of ${symbolCount} by size of net)`;

  // Earnings' arm registry is not in the console's retired-arms reader yet; read it if it ever is.
  const retired = (readRetiredArms(config).retired as Record<string, string[] | undefined>)["earnings"] ?? [];

  const era = suiteEra(config.paths.orchestratorConfig).from;
  const notes = [
    "A trade here is one defined-risk structure (iron fly, iron condor, calendar, butterfly or credit spread) opened on one name around its earnings report, booked to the session it closed in.",
    "Gross is the structure's price change; fees, settlement and slippage come off it separately to reach net. Paper fills are at mid, so slippage is charged as a cost.",
    "The arms are the books a trade belongs to (strat_test for each structure, plus advised twins); the structure table groups the same trades by their shape.",
    `Counted from the suite's data epoch${era !== null ? ` (${era})` : ""} by entry date, as the earnings history page is.`,
  ];
  if (mode === "live") notes.push("The live ledger records less than paper: exit reason, slippage, settlement and sessions held read n/r there.");

  return {
    module: "earnings",
    mode,
    window,
    session,
    unit: UNIT,
    hero,
    tiles,
    gauges,
    breakdowns: [byStrategy, byExit, bySymbol],
    arms: armsOf(trades, retired),
    notes,
  };
}
