import path from "node:path";
import { feeDragPct, type ModuleSummarySlice, type SummaryGauge, type SummaryTile } from "@console/shared";
import type { ConsoleConfig } from "../../config.js";
import { readRetiredArms } from "../armStatus.js";
import { rangeClauses } from "../dateRange.js";
import { hasColumn, num, str, withReadSnapshot, type DatabaseHandle } from "../db.js";
import { CURRENT_ERA, eraClause } from "../flies.js";
import {
  WEEKDAYS,
  armsOf,
  breakdownOf,
  emptySlice,
  heroOf,
  sessionNeighbours,
  summaryWindow,
  weekdayOf,
  type SummaryQuery,
  type SummaryTrade,
} from "./common.js";

/**
 * Flies's simplified-view summary: the legged 0DTE butterfly book judged the way the module judges
 * itself (packages/flies/CLAUDE.md, "five measurements"): how often a leg-in became a fly, what
 * the strands cost against what the completions earned, the floor the completions locked, and the
 * fee drag that drove the SPX switch.
 *
 * Money is `readFliesTradeLog`'s per-row arithmetic (`tradeCash`), row for row: gross = gross_pnl,
 * trading fees = the fee total less settlement (the total where the split was never recorded),
 * settlement = settlement_fees, net = pnl. Slippage is inside gross (the modelled fill concedes it),
 * so it is never a cost line. Populations are the trade log's: settled, not voided, scoped to the
 * current era by default (`eraClause(null)`), booked to `trade_date`.
 */

const MODULE = "flies";
const UNIT = "a legged 0DTE fly (or the vertical it stranded as)";
/** The trade log's settled population (flies.ts `SETTLED`). */
const SETTLED = "status = 'settled' AND void_reason IS NULL";
/** Paper legged completions pay the live limit from this session (flies CLAUDE.md): never pool
 *  completion P&L across it. */
const COMPLETION_RULE_BREAK = "2026-10-05";

const OUTCOMES = ["completed fly", "stranded vertical", "unrolled bwb", "outright"] as const;

interface FlyTrade extends SummaryTrade {
  entryMode: string | null;
  kind: string | null;
  outcome: string;
  hour: string;
  premium: number;
  floor: number | null;
}

const round2 = (v: number) => Math.round(v * 100) / 100;

/** How a settled position ended, in the module's words. A rolled bwb and a completed leg-in both
 *  end holding a symmetric fly (`kind = 'fly'`), which is the point of each. */
function outcomeOf(entryMode: string | null, kind: string | null): string {
  if (entryMode === "outright") return "outright";
  if (kind === "fly") return "completed fly";
  if (kind !== null && kind.endsWith("vertical")) return "stranded vertical";
  if (kind === "bwb") return "unrolled bwb";
  return kind ?? "unknown";
}

/** Entry clock hour off the stored ISO string's own market offset — `flies.ts` `entryHour`'s rule
 *  (slice, never parse through a viewer-local Date). Malformed buckets to "unknown". */
function entryHour(iso: string | null): string {
  if (iso === null || iso.length < 16) return "unknown";
  const hh = iso.slice(11, 13);
  const hour = Number(hh);
  if (!Number.isInteger(hour)) return "unknown";
  return `${hh}:00-${String(hour + 1).padStart(2, "0")}:00`;
}

function col(db: DatabaseHandle, name: string): string {
  return hasColumn(db, "fly_positions", name) ? name : "NULL";
}

export function readFliesSummary(config: ConsoleConfig, q: SummaryQuery): ModuleSummarySlice {
  const file = q.mode === "live" ? "live_trades.db" : "paper_trades.db";
  const dbPath = path.join(config.paths.fliesDir, file);
  const missing = emptySlice(MODULE, q.mode, UNIT, summaryWindow(q.range, q.day));
  missing.notes.push(`No ${q.mode} flies ledger could be read, so there is nothing to summarise.`);

  return withReadSnapshot<ModuleSummarySlice>(dbPath, missing, (db) => {
    const era = eraClause(null);
    const eraAnd = era.sql !== null ? ` AND ${era.sql}` : "";

    // Sessions with settled rows in the era: the day arrows, and the latest one when no day is named.
    const sessions = db
      .prepare<string[], { d: string }>(
        `SELECT DISTINCT trade_date AS d FROM fly_positions
          WHERE ${SETTLED}${eraAnd} AND trade_date IS NOT NULL ORDER BY d`,
      )
      .all(...era.params)
      .map((r) => r.d);
    const session = sessionNeighbours(sessions, q.day);
    const win = summaryWindow(q.range, session.day);
    const rc = rangeClauses("trade_date", { from: win.from, to: win.to });
    const winAnd = rc.clauses.map((c) => ` AND ${c}`).join("");
    const params = [...era.params, ...rc.params];

    const rows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT trade_date, entry_time, arm, entry_mode, kind, quantity, net, gross_pnl, fees,
                ${col(db, "settlement_fees")} AS settlement_fees,
                ${col(db, "floor_dollars")} AS floor_dollars, pnl
           FROM fly_positions WHERE ${SETTLED}${eraAnd}${winAnd}`,
      )
      .all(...params);

    const trades: FlyTrade[] = rows.map((r) => {
      const total = num(r["fees"]);
      const settlement = num(r["settlement_fees"]);
      const entryMode = str(r["entry_mode"]);
      const kind = str(r["kind"]);
      const price = num(r["net"]);
      return {
        session: String(r["trade_date"]),
        arm: str(r["arm"]) ?? "unknown",
        gross: num(r["gross_pnl"]) ?? 0,
        // tradeCash's rule: settlement out of the total once; the total stays when never split.
        fees: total !== null && settlement !== null ? round2(total - settlement) : total,
        settlement,
        slippage: null,
        net: num(r["pnl"]) ?? 0,
        entryMode,
        kind,
        outcome: outcomeOf(entryMode, kind),
        hour: entryHour(str(r["entry_time"])),
        // Premium collected: the history reader's credit-fly rule (pnlRows): the entry credit,
        // whole position; a debit entry collects nothing.
        premium: Math.max(price ?? 0, 0) * 100 * (num(r["quantity"]) ?? 1),
        floor: kind === "fly" ? num(r["floor_dollars"]) : null,
      };
    });

    const open = Number(
      db
        .prepare<string[], { n: number }>(
          `SELECT COUNT(*) AS n FROM fly_positions
            WHERE COALESCE(status, '') NOT IN ('settled', 'cancelled') AND void_reason IS NULL${eraAnd}${winAnd}`,
        )
        .get(...params)?.n ?? 0,
    );

    const hero = heroOf(trades, false, open);
    const premium = round2(trades.reduce((s, t) => s + t.premium, 0));
    hero.basis = trades.length > 0 ? { label: "premium collected", amount: premium } : null;

    // -- Completion: legged entries only, the performance tab's population (debit-first and bwb
    //    rolls convert by their own rules and are reported apart there). Settled rows only, so a
    //    leg-in still working today is not counted as a miss.
    const legged = trades.filter((t) => t.entryMode === "legged");
    const completedLegged = legged.filter((t) => t.kind === "fly").length;
    const gauges: SummaryGauge[] = [
      {
        title: "completion rate",
        rawPct: legged.length > 0 ? (completedLegged / legged.length) * 100 : null,
        min: 0,
        max: 100,
        caption:
          legged.length > 0
            ? `${completedLegged} of ${legged.length} settled legged entries became a fly`
            : "no settled legged entries in this window",
      },
    ];

    const completed = trades.filter((t) => t.outcome === "completed fly");
    const stranded = trades.filter((t) => t.outcome === "stranded vertical");
    const sumNet = (ts: readonly FlyTrade[]) => round2(ts.reduce((s, t) => s + t.net, 0));
    const floorKnown = completed.map((t) => t.floor).filter((f): f is number => f !== null);
    const tradingFees = hero.costs.find((c) => c.label === "fees")?.amount ?? null;

    const tiles: SummaryTile[] = [
      {
        label: "completed flies net",
        value: completed.length > 0 ? sumNet(completed) : null,
        unit: "usd",
        note: `${completed.length} fl${completed.length === 1 ? "y" : "ies"}, after fees`,
      },
      {
        label: "stranded verticals net",
        value: stranded.length > 0 ? sumNet(stranded) : null,
        unit: "usd",
        note: `${stranded.length} vertical${stranded.length === 1 ? "" : "s"} never completed, after fees`,
      },
      {
        label: "floor locked by completions",
        value: floorKnown.length > 0 ? round2(floorKnown.reduce((s, f) => s + f, 0)) : null,
        unit: "usd",
        note:
          completed.length === 0
            ? "no completed flies"
            : `guaranteed worst case of the completed flies, after fees (${floorKnown.length}/${completed.length} recorded)`,
      },
      {
        label: "fee drag",
        value: feeDragPct(tradingFees, premium),
        unit: "pct",
        note: `trading fees ÷ $${premium.toFixed(2)} premium collected`,
        signed: false,
      },
    ];

    const extraOutcomes = [...new Set(trades.map((t) => t.outcome))]
      .filter((o) => !(OUTCOMES as readonly string[]).includes(o))
      .sort();
    const breakdowns = [
      breakdownOf("by outcome", "outcome", trades, (t) => t.outcome, [...OUTCOMES, ...extraOutcomes]),
      breakdownOf("by entry hour (ET)", "entry hour", trades, (t) => t.hour),
      breakdownOf("by weekday", "weekday", trades, (t) => weekdayOf(t.session), WEEKDAYS),
    ];

    const notes: string[] = [
      "A trade is one leg-in: a credit spread sold first, then the opposite spread bought to complete a butterfly if the market offers it cheaply enough. A completed fly cannot lose more than its locked floor; an entry that never completed is a stranded vertical carrying its full defined risk.",
      q.mode === "live"
        ? "Gross is at the broker's fill prices. Slippage is already inside those prices, so it is not charged again."
        : "Gross is at the modelled fill, which already gives up the bid/ask, so slippage is not charged again as a cost.",
      `Counts the current SPX era only (from ${CURRENT_ERA.from}); the earlier XSP and SPX books are a different trade and are not pooled in.`,
      "Trades on the same day share one market, so read the session count, not the trade count, as the sample size.",
      "The floor is the guaranteed worst case once a fly completed, net of fees and the settlement fee it would owe; it is not the result.",
    ];
    if (
      q.mode === "paper" &&
      (win.from === null || win.from < COMPLETION_RULE_BREAK) &&
      (win.to === null || win.to >= COMPLETION_RULE_BREAK)
    ) {
      notes.push(
        `This window spans ${COMPLETION_RULE_BREAK}, when paper completions began paying the live limit price; completion P&L either side of that date is not like for like.`,
      );
    }

    return {
      module: MODULE,
      mode: q.mode,
      window: win,
      session,
      unit: UNIT,
      hero,
      tiles,
      gauges,
      breakdowns,
      arms: armsOf(trades, readRetiredArms(config).retired.flies ?? []),
      notes,
    };
  });
}
