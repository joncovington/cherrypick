/**
 * Signed options flow and the flow-adjusted GEX: a mirror of `cherrypick.core.gex.print_sign`,
 * `signed_flow` and `flow_adjusted` (2026-11-01). The classification runs in SQL over gex's
 * `gex_prints` -- a session holds up to a million prints, so they are summed per strike in the
 * database, never fetched -- and `packages/core/tests/fixtures/gex_flow_cases.json` pins this and the
 * core to the same hand-worked answers (`server/test/gex-flow.test.ts`). Change both or neither.
 *
 * Display only. Under every GEX number sits the assumption that customers are long the puts and short
 * the calls, dealers the other side; a customer's net buy of either right leaves the dealer that much
 * shorter. An initiating buyer may be closing, not opening: the panel says so.
 */

import type Database from "better-sqlite3";
import { dollarGamma } from "./gex.js";

/** `gex_prints.side`, as gex's `QUOTE_SIDES` writes it. */
const SIDE_NAME: Record<number, "call" | "put"> = { 0: "put", 1: "call" };

/** The print's sign, as `core.gex.print_sign`: 1 buy, -1 sell, 0 unclassified, NULL not counted. */
const SIGN_SQL = `CASE
  WHEN kind NOT IN ('NEW', 'CORRECTION') THEN NULL
  WHEN valid_tick = 0 OR eth = 1 OR spread_leg = 1 THEN NULL
  WHEN size IS NULL OR size <= 0 THEN NULL
  WHEN UPPER(COALESCE(aggressor, '')) = 'BUY' THEN 1
  WHEN UPPER(COALESCE(aggressor, '')) = 'SELL' THEN -1
  WHEN bid IS NULL OR ask IS NULL OR price IS NULL OR ask <= bid THEN 0
  WHEN price >= ask THEN 1
  WHEN price <= bid THEN -1
  ELSE 0 END`;

/** Why an uncounted print was left out, in `signed_flow`'s order of precedence. */
const EXCLUDED_SQL = `CASE
  WHEN kind NOT IN ('NEW', 'CORRECTION') THEN 'not_traded'
  WHEN valid_tick = 0 THEN 'invalid'
  WHEN eth = 1 THEN 'extended_hours'
  WHEN spread_leg = 1 THEN 'spread_leg'
  ELSE NULL END`;

export interface FlowStrike {
  side: "call" | "put";
  strike: number;
  bought: number;
  sold: number;
  unclassified: number;
  net: number;
}

export interface SignedFlow {
  strikes: FlowStrike[];
  classified_by: { aggressor: number; quote: number; unclassified: number };
  excluded: { not_traded: number; invalid: number; extended_hours: number; spread_leg: number };
}

/** `signed_flow` over one session's prints of one expiration, summed in SQL. */
export function readSignedFlow(db: Database.Database, symbol: string, expiration: string, tradeDate: string): SignedFlow {
  const where = "symbol = ? AND expiration = ? AND trade_date = ?";
  const args = [symbol, expiration, tradeDate];
  const rows = db
    .prepare<string[], { side: number; strike: number; bought: number; sold: number; unclassified: number }>(
      `WITH p AS (SELECT side, strike, size, ${SIGN_SQL} AS sign FROM gex_prints WHERE ${where})
       SELECT side, strike,
              SUM(CASE WHEN sign = 1 THEN size ELSE 0 END) AS bought,
              SUM(CASE WHEN sign = -1 THEN size ELSE 0 END) AS sold,
              SUM(CASE WHEN sign = 0 THEN size ELSE 0 END) AS unclassified
         FROM p WHERE sign IS NOT NULL GROUP BY side, strike ORDER BY strike, side DESC`,
    )
    .all(...args);
  const by = db
    .prepare<string[], { aggressor: number | null; quote: number | null; unclassified: number | null }>(
      `WITH p AS (SELECT size, aggressor, ${SIGN_SQL} AS sign FROM gex_prints WHERE ${where})
       SELECT SUM(CASE WHEN sign IN (1, -1) AND UPPER(COALESCE(aggressor, '')) IN ('BUY', 'SELL') THEN size ELSE 0 END) AS aggressor,
              SUM(CASE WHEN sign IN (1, -1) AND UPPER(COALESCE(aggressor, '')) NOT IN ('BUY', 'SELL') THEN size ELSE 0 END) AS quote,
              SUM(CASE WHEN sign = 0 THEN size ELSE 0 END) AS unclassified
         FROM p`,
    )
    .get(...args);
  const excluded = { not_traded: 0, invalid: 0, extended_hours: 0, spread_leg: 0 };
  const ex = db
    .prepare<string[], { why: keyof typeof excluded; n: number }>(
      `SELECT ${EXCLUDED_SQL} AS why, SUM(COALESCE(size, 0)) AS n FROM gex_prints WHERE ${where} GROUP BY why`,
    )
    .all(...args);
  for (const r of ex) if (r.why !== null && r.why in excluded) excluded[r.why] = r.n;
  return {
    strikes: rows.map((r) => ({
      side: SIDE_NAME[r.side] ?? "put",
      strike: r.strike,
      bought: r.bought,
      sold: r.sold,
      unclassified: r.unclassified,
      net: r.bought - r.sold,
    })),
    classified_by: { aggressor: by?.aggressor ?? 0, quote: by?.quote ?? 0, unclassified: by?.unclassified ?? 0 },
    excluded,
  };
}

export interface FlowAdjustedRow {
  strike: number;
  net_gex: number;
  flow_gex: number;
  net_gex_flow: number;
}

/**
 * `flow_adjusted`: per strike, the OI net GEX (gamma x (call OI - put OI)), the flow's GEX
 * (-gamma x the net bought of either right) and their sum. `gamma` is the strike's one gamma.
 */
export function flowAdjusted(
  strikes: Array<{ strike: number; gamma: number; call_oi: number; put_oi: number }>,
  flow: FlowStrike[],
  spot: number,
  multiplier = 100,
): FlowAdjustedRow[] {
  const netBought = new Map<number, number>();
  for (const f of flow) netBought.set(f.strike, (netBought.get(f.strike) ?? 0) + f.net);
  return strikes.map((s) => {
    const oiNet = dollarGamma(s.gamma, s.call_oi, multiplier, spot) - dollarGamma(s.gamma, s.put_oi, multiplier, spot);
    const flowGex = -dollarGamma(s.gamma, netBought.get(s.strike) ?? 0, multiplier, spot);
    // `+ 0` folds -0 into 0, as Python's round() does.
    return { strike: s.strike, net_gex: Math.round(oiNet) + 0, flow_gex: Math.round(flowGex) + 0, net_gex_flow: Math.round(oiNet + flowGex) + 0 };
  });
}
