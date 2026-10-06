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
  const fills = new Map<string, number>();
  for (const r of rows) {
    if (str(r["outcome"]) === "filled") fills.set(str(r["arm"]) ?? "", (fills.get(str(r["arm"]) ?? "") ?? 0) + 1);
    const arm = str(r["arm"]) ?? "";
    const key = `${arm}|${str(r["trade_date"]) ?? ""}`;
    const outcome = str(r["outcome"]) ?? "unknown";
    const prev = last.get(key);
    last.set(key, { arm, filled: (prev?.filled ?? false) || outcome === "filled", outcome });
  }
  const out = new Map<string, EntryOutcomes>();
  for (const s of last.values()) {
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
 * slippage is a recorded measure -- shown, never subtracted again (root CLAUDE.md). So here
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
