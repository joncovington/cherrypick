import type { ArmMoneyRow, EntryOutcomes } from "@console/shared";
import { hasTable, memoOnStore, num, str, type DatabaseHandle } from "./db.js";
import { positionCash, tradeTotals } from "./positionCash.js";

/**
 * The two reads every module's costs page needs, shared since pmcc got one (2026-10-06) after curve:
 *
 * - `armMoney`: each arm's closed positions in the money layout, split through `positionCash` (the
 *   history table's own arithmetic -- the ledger's `fees` is the TOTAL cost), with the premium the
 *   costs are judged against.
 * - `entryOutcomes`: entry outcomes per SESSION, not per tick. An entry window that ticks every
 *   minute records a row per tick, so a raw count weights a gate by how many ticks it refused, not
 *   how many sessions it cost. A session is entered if any tick filled, else it counts under its
 *   last refusal.
 */

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

/** `rows` are closed positions carrying `arm`, `positionCash`'s columns, the signed per-share entry
 *  price under `entry`, and the premium received (whole-position dollars) under `premium`. */
export function armMoney(rows: Array<Record<string, unknown>>): ArmMoneyRow[] {
  const byArm = new Map<string, { cash: ReturnType<typeof positionCash>[]; premium: number }>();
  for (const r of rows) {
    const arm = str(r["arm"]) ?? "";
    const t = byArm.get(arm) ?? { cash: [], premium: 0 };
    t.cash.push(positionCash(r, num(r["entry"])));
    t.premium += Math.max(0, num(r["premium"]) ?? 0);
    byArm.set(arm, t);
  }
  return [...byArm.entries()].map(([arm, { cash, premium }]) => {
    const t = tradeTotals(cash);
    const wins = cash.filter((c) => (c.netPnl ?? 0) > 0).length;
    return {
      arm,
      positions: t.positions,
      wins,
      premium: round2(premium),
      grossPnl: t.gross,
      fees: t.fees,
      settlementFees: t.settlementFees,
      slippage: t.slippage,
      netPnl: t.net,
      winRate: t.positions > 0 ? wins / t.positions : null,
    };
  });
}

interface AttemptSession {
  arm: string;
  tradeDate: string;
  /** The outcome of the session's last attempt (highest id). */
  outcome: string;
  fills: number;
}

/**
 * One row per (arm, session): the last attempt's outcome and the session's fill count. Grouped in
 * SQL, not by walking every attempt in id order: flies' table is ~508k rows (a row per tick per
 * arm), and the walk ran twice per request (era and all). `outcome` is a bare column beside
 * `MAX(id)`, which SQLite documents as taken from the row holding the max -- the last attempt.
 * The (arm, trade_date, outcome) index flies adds serves this as a covering scan in group order.
 * Memoised on the store's stamp, so the era and all-time reads share one pass.
 */
function attemptSessions(db: DatabaseHandle, table: string): AttemptSession[] {
  // An in-memory handle has no file stamp to key on (every one is named ":memory:").
  if (db.memory) return groupAttempts(db, table);
  return memoOnStore(db.name, `entryOutcomes:${db.name}:${table}`, () => groupAttempts(db, table));
}

function groupAttempts(db: DatabaseHandle, table: string): AttemptSession[] {
  return db
      .prepare<[], Record<string, unknown>>(
        `SELECT arm, trade_date, outcome, MAX(id) AS last_id, SUM(outcome = 'filled') AS fills
           FROM ${table} WHERE trade_date IS NOT NULL GROUP BY arm, trade_date`,
      )
      .all()
      .map((r) => ({
        arm: str(r["arm"]) ?? "",
        tradeDate: str(r["trade_date"]) ?? "",
        outcome: str(r["outcome"]) ?? "unknown",
        fills: Number(r["fills"] ?? 0),
      }));
}

/** Entry outcomes per arm per session from `table` (an `*_entry_attempts` ledger table), from
 *  `since` (inclusive) when given. */
export function entryOutcomes(db: DatabaseHandle, table: string, since: string | null): EntryOutcomes[] {
  if (!hasTable(db, table)) return [];
  const last: Array<{ arm: string; filled: boolean; outcome: string }> = [];
  const fills = new Map<string, number>();
  for (const s of attemptSessions(db, table)) {
    if (s.tradeDate < (since ?? "")) continue;
    fills.set(s.arm, (fills.get(s.arm) ?? 0) + s.fills);
    last.push({ arm: s.arm, filled: s.fills > 0, outcome: s.outcome });
  }
  const out = new Map<string, EntryOutcomes>();
  for (const s of last) {
    const o = out.get(s.arm) ?? { arm: s.arm, sessions: 0, entered: 0, refusals: {}, fills: 0 };
    o.sessions += 1;
    if (s.filled) o.entered += 1;
    else o.refusals[s.outcome] = (o.refusals[s.outcome] ?? 0) + 1;
    out.set(s.arm, o);
  }
  for (const o of out.values()) o.fills = fills.get(o.arm) ?? 0;
  return [...out.values()].sort((a, b) => a.arm.localeCompare(b.arm));
}

/**
 * Per-arm money for a module whose modelled fill already CONCEDES slippage (flies, meic): gross is
 * after the concession, `fees` in the ledger is the total of trading fees and settlement, and
 * slippage is a recorded measure -- shown, never subtracted again (docs/conventions/money-layout.md). So here
 * gross - fees - settlement = net, and `slippage` stands beside the row, not in it.
 * `rows`: `arm`, `gross`, `fees_total`, `settlement_fees`, `slippage_dollars`, `premium`.
 */
export function concededArmMoney(rows: Array<Record<string, unknown>>): ArmMoneyRow[] {
  const byArm = new Map<string, ArmMoneyRow>();
  for (const r of rows) {
    const arm = str(r["arm"]) ?? "—";
    const gross = num(r["gross"]) ?? 0;
    const total = num(r["fees_total"]) ?? 0;
    const settle = num(r["settlement_fees"]) ?? 0;
    const t = byArm.get(arm) ?? {
      arm, positions: 0, wins: 0, premium: 0, grossPnl: 0, fees: 0, settlementFees: 0, slippage: 0, netPnl: 0, winRate: null,
    };
    t.positions += 1;
    t.wins += gross - total > 0 ? 1 : 0;
    t.premium = round2(t.premium + Math.max(0, num(r["premium"]) ?? 0));
    t.grossPnl = round2(t.grossPnl + gross);
    t.fees = round2(t.fees + total - settle);
    t.settlementFees = round2(t.settlementFees + settle);
    t.slippage = round2(t.slippage + (num(r["slippage_dollars"]) ?? 0));
    t.netPnl = round2(t.netPnl + gross - total);
    t.winRate = t.wins / t.positions;
    byArm.set(arm, t);
  }
  return [...byArm.values()].sort((a, b) => a.arm.localeCompare(b.arm));
}
