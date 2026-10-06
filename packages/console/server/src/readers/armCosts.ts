import type { ArmMoneyRow, EntryOutcomes } from "@console/shared";
import { hasTable, num, str, type DatabaseHandle } from "./db.js";
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

/** Entry outcomes per arm per session from `table` (an `*_entry_attempts` ledger table), from
 *  `since` (inclusive) when given. */
export function entryOutcomes(db: DatabaseHandle, table: string, since: string | null): EntryOutcomes[] {
  if (!hasTable(db, table)) return [];
  const rows = db
    .prepare<[string], Record<string, unknown>>(`SELECT arm, trade_date, outcome FROM ${table} WHERE trade_date >= ? ORDER BY id`)
    .all(since ?? "");
  const last = new Map<string, { arm: string; filled: boolean; outcome: string }>();
  for (const r of rows) {
    const arm = str(r["arm"]) ?? "";
    const key = `${arm}|${str(r["trade_date"]) ?? ""}`;
    const outcome = str(r["outcome"]) ?? "unknown";
    const prev = last.get(key);
    last.set(key, { arm, filled: (prev?.filled ?? false) || outcome === "filled", outcome });
  }
  const out = new Map<string, EntryOutcomes>();
  for (const s of last.values()) {
    const o = out.get(s.arm) ?? { arm: s.arm, sessions: 0, entered: 0, refusals: {} };
    o.sessions += 1;
    if (s.filled) o.entered += 1;
    else o.refusals[s.outcome] = (o.refusals[s.outcome] ?? 0) + 1;
    out.set(s.arm, o);
  }
  return [...out.values()].sort((a, b) => a.arm.localeCompare(b.arm));
}
