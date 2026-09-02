/**
 * The earnings module's OPEN positions, as the managed loop sees them.
 *
 * Before the lifecycle change there was nothing to show here: every position was force-closed the
 * morning after entry, so "open" only ever meant "entered a few hours ago and not yet swept". A
 * winner can now be carried for up to three sessions, which makes what it is worth mid-flight — and
 * whether the loop marking it is even alive — a thing worth looking at.
 *
 * Read-only, like every other console reader. The earnings module owns this database; nothing here
 * writes to it, and a missing table degrades to an empty list rather than failing the page.
 */
import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, num, str } from "./db.js";
import { isoStamp } from "../services/report.js";

export interface EarningsMark {
  markedAt: string | null;
  exitDebit: number | null;
  unrealizedPnl: number | null;
  spot: number | null;
  source: string | null;
  maxLegSpreadPct: number | null;
}

export interface EarningsOpenPosition {
  orderId: string;
  symbol: string;
  strategy: string;
  expiration: string | null;
  entryCredit: number | null;
  quantity: number | null;
  capitalAtRisk: number | null;
  openedAt: string | null;
  status: string | null;
  closeAttempts: number | null;
  maxUnrealizedPnl: number | null;
  minUnrealizedPnl: number | null;
  /** Latest USABLE mark. A refused one records that we looked and could not price it — real, but
   *  not a valuation, so it never becomes the number on screen. */
  mark: EarningsMark | null;
  /** Why the loop last left it alone, or what it is waiting on. */
  lastEvent: EarningsEvent | null;
}

export interface EarningsEvent {
  orderId: string;
  occurredAt: string | null;
  phase: string | null;
  action: string;
  reason: string;
  executed: boolean;
  gate: string | null;
}

export interface EarningsLoopStatus {
  ranAt: string | null;
  phase: string | null;
  status: string | null;
  openPositions: number | null;
  marksWritten: number | null;
  actionsTaken: number | null;
  quotesFresh: number | null;
  quotesStale: number | null;
  openCapital: number | null;
  note: string | null;
}

export interface EarningsLivePayload {
  positions: EarningsOpenPosition[];
  events: EarningsEvent[];
  loop: EarningsLoopStatus | null;
  openCapital: number;
  generatedAt: string;
}

function tableExists(db: import("better-sqlite3").Database, name: string): boolean {
  return (
    db
      .prepare<[string], Record<string, unknown>>(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
      )
      .get(name) !== undefined
  );
}

export function readEarningsLive(config: ConsoleConfig): EarningsLivePayload {
  const dbPath = path.join(config.paths.earningsDir, "paper_trades.db");
  const empty: EarningsLivePayload = {
    positions: [],
    events: [],
    loop: null,
    openCapital: 0,
    generatedAt: new Date().toISOString(),
  };

  return withReadOnlyDb<EarningsLivePayload>(dbPath, empty, (db) => {
    const hasMarks = tableExists(db, "position_marks");
    const hasEvents = tableExists(db, "management_events");
    const hasIterations = tableExists(db, "loop_iterations");

    const rows = db
      .prepare<[], Record<string, unknown>>(
        `SELECT order_id, symbol, strategy, expiration, entry_credit, quantity, capital_at_risk,
                opened_at, status, close_attempts, max_unrealized_pnl, min_unrealized_pnl
           FROM trades WHERE closed_at IS NULL ORDER BY opened_at DESC`,
      )
      .all();

    // Latest usable mark per position. Ordered newest-first and taken one at a time rather than
    // grouped, so a position whose recent marks were all refused falls back to its last real one
    // instead of showing nothing.
    const markFor = hasMarks
      ? db.prepare<[string], Record<string, unknown>>(
          `SELECT marked_at, exit_debit, unrealized_pnl, spot, source, max_leg_spread_pct
             FROM position_marks WHERE order_id = ? AND usable = 1
            ORDER BY marked_at DESC LIMIT 1`,
        )
      : null;
    const eventFor = hasEvents
      ? db.prepare<[string], Record<string, unknown>>(
          `SELECT order_id, occurred_at, phase, action, reason, executed, gate
             FROM management_events WHERE order_id = ? ORDER BY occurred_at DESC LIMIT 1`,
        )
      : null;

    const positions: EarningsOpenPosition[] = rows.map((r) => {
      const orderId = String(r["order_id"] ?? "");
      const m = markFor?.get(orderId);
      const e = eventFor?.get(orderId);
      return {
        orderId,
        symbol: str(r["symbol"]) ?? "",
        strategy: str(r["strategy"]) ?? "",
        expiration: str(r["expiration"]),
        entryCredit: num(r["entry_credit"]),
        quantity: num(r["quantity"]),
        capitalAtRisk: num(r["capital_at_risk"]),
        openedAt: isoStamp(r["opened_at"]),
        status: str(r["status"]),
        closeAttempts: num(r["close_attempts"]),
        maxUnrealizedPnl: num(r["max_unrealized_pnl"]),
        minUnrealizedPnl: num(r["min_unrealized_pnl"]),
        mark: m
          ? {
              markedAt: isoStamp(m["marked_at"]),
              exitDebit: num(m["exit_debit"]),
              unrealizedPnl: num(m["unrealized_pnl"]),
              spot: num(m["spot"]),
              source: str(m["source"]),
              maxLegSpreadPct: num(m["max_leg_spread_pct"]),
            }
          : null,
        lastEvent: e
          ? {
              orderId,
              occurredAt: isoStamp(e["occurred_at"]),
              phase: str(e["phase"]),
              action: str(e["action"]) ?? "",
              reason: str(e["reason"]) ?? "",
              executed: Boolean(num(e["executed"])),
              gate: str(e["gate"]),
            }
          : null,
      };
    });

    const events: EarningsEvent[] = hasEvents
      ? db
          .prepare<[], Record<string, unknown>>(
            `SELECT order_id, occurred_at, phase, action, reason, executed, gate
               FROM management_events ORDER BY occurred_at DESC LIMIT 100`,
          )
          .all()
          .map((e) => ({
            orderId: String(e["order_id"] ?? ""),
            occurredAt: isoStamp(e["occurred_at"]),
            phase: str(e["phase"]),
            action: str(e["action"]) ?? "",
            reason: str(e["reason"]) ?? "",
            executed: Boolean(num(e["executed"])),
            gate: str(e["gate"]),
          }))
      : [];

    // The loop's own vital signs: what makes a live-but-quiet loop distinguishable from a dead one
    // without reading logs.
    const iteration = hasIterations
      ? db
          .prepare<[], Record<string, unknown>>(
            `SELECT ran_at, phase, status, open_positions, marks_written, actions_taken,
                    quotes_fresh, quotes_stale, open_capital, note
               FROM loop_iterations ORDER BY ran_at DESC LIMIT 1`,
          )
          .get()
      : undefined;

    return {
      positions,
      events,
      loop: iteration
        ? {
            ranAt: isoStamp(iteration["ran_at"]),
            phase: str(iteration["phase"]),
            status: str(iteration["status"]),
            openPositions: num(iteration["open_positions"]),
            marksWritten: num(iteration["marks_written"]),
            actionsTaken: num(iteration["actions_taken"]),
            quotesFresh: num(iteration["quotes_fresh"]),
            quotesStale: num(iteration["quotes_stale"]),
            openCapital: num(iteration["open_capital"]),
            note: str(iteration["note"]),
          }
        : null,
      openCapital: positions.reduce((sum, p) => sum + (p.capitalAtRisk ?? 0), 0),
      generatedAt: new Date().toISOString(),
    };
  });
}
