/**
 * Port of the orchestrator report's cross-module paper P&L rollup — the
 * per-schema net rules are the load-bearing part and copied exactly:
 *   meic ic_trades:   net = pnl − fees (resolved trades)
 *   earnings trades:  net = pnl − entry_cost − exit_cost (closed)
 *   flies fly_books:  net = pnl (book P&L is already net of fees when settled)
 * Sessions keyed by trade date; suite stats = net, trades, wins/losses on the
 * per-trade net, win rate, average.
 */

import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb } from "../readers/db.js";

interface TradeNet {
  session: string;
  net: number;
}

function meicNets(config: ConsoleConfig): TradeNet[] {
  const dbPath = path.join(config.paths.meicDir, "paper_trades.db");
  return withReadOnlyDb<TradeNet[]>(dbPath, [], (db) =>
    db
      .prepare<[], Record<string, unknown>>(
        `SELECT trade_date, pnl, fees FROM ic_trades
          WHERE pnl IS NOT NULL AND status NOT IN ('cancelled','pending','partial_entry')`,
      )
      .all()
      .map((r) => ({ session: String(r["trade_date"]), net: Number(r["pnl"]) - Number(r["fees"] ?? 0) })),
  );
}

/**
 * Earnings timestamps are stored as epoch-seconds floats in some columns and
 * ISO strings in others — normalize either to a full ISO stamp. Reading one as
 * the other is not a formatting slip: a float read as a string is null, and a
 * null closed_at means "still open".
 */
export function isoStamp(v: unknown): string | null {
  if (typeof v === "number" && Number.isFinite(v)) return new Date(v * 1000).toISOString();
  const s = String(v ?? "");
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s;
  const n = Number.parseFloat(s);
  if (Number.isFinite(n) && n > 1e9) return new Date(n * 1000).toISOString();
  return null;
}

/** The same value narrowed to its session, YYYY-MM-DD. */
export function sessionDate(closedAt: unknown): string | null {
  return isoStamp(closedAt)?.slice(0, 10) ?? null;
}

function earningsNets(config: ConsoleConfig): TradeNet[] {
  const dbPath = path.join(config.paths.earningsDir, "paper_trades.db");
  return withReadOnlyDb<TradeNet[]>(dbPath, [], (db) =>
    db
      .prepare<[], Record<string, unknown>>(
        `SELECT closed_at, pnl, entry_cost, exit_cost FROM trades WHERE closed_at IS NOT NULL AND pnl IS NOT NULL`,
      )
      .all()
      .flatMap((r) => {
        const session = sessionDate(r["closed_at"]);
        if (session === null) return [];
        return [{ session, net: Number(r["pnl"]) - Number(r["entry_cost"] ?? 0) - Number(r["exit_cost"] ?? 0) }];
      }),
  );
}

function fliesNets(config: ConsoleConfig): TradeNet[] {
  const dbPath = path.join(config.paths.fliesDir, "paper_trades.db");
  return withReadOnlyDb<TradeNet[]>(dbPath, [], (db) =>
    db
      .prepare<[], Record<string, unknown>>(
        `SELECT trade_date, pnl FROM fly_books WHERE pnl IS NOT NULL AND status = 'settled'`,
      )
      .all()
      .map((r) => ({ session: String(r["trade_date"]), net: Number(r["pnl"]) })),
  );
}

export interface SuiteReport {
  suite: { net: number; trades: number; wins: number; losses: number; winRatePct: number | null; avg: number | null };
  /** Sessions ascending; cumulative suite equity plus per-module cumulative lines. */
  daily: Array<{ session: string; net: number; cumulative: number; byModule: Record<string, number> }>;
  modules: Record<string, { net: number; trades: number; wins: number; losses: number }>;
}

export function buildSuiteReport(config: ConsoleConfig): SuiteReport {
  const byModule: Record<string, TradeNet[]> = {
    meic: meicNets(config),
    earnings: earningsNets(config),
    flies: fliesNets(config),
  };

  const sessions = new Map<string, { net: number; byModule: Record<string, number> }>();
  const modules: SuiteReport["modules"] = {};
  let net = 0;
  let trades = 0;
  let wins = 0;
  let losses = 0;

  for (const [mod, nets] of Object.entries(byModule)) {
    const m = { net: 0, trades: 0, wins: 0, losses: 0 };
    for (const t of nets) {
      m.net += t.net;
      m.trades += 1;
      if (t.net > 0) m.wins += 1;
      else if (t.net < 0) m.losses += 1;
      let s = sessions.get(t.session);
      if (s === undefined) {
        s = { net: 0, byModule: {} };
        sessions.set(t.session, s);
      }
      s.net += t.net;
      s.byModule[mod] = (s.byModule[mod] ?? 0) + t.net;
    }
    modules[mod] = m;
    net += m.net;
    trades += m.trades;
    wins += m.wins;
    losses += m.losses;
  }

  let cumulative = 0;
  const daily = [...sessions.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([session, s]) => {
      cumulative += s.net;
      return { session, net: s.net, cumulative, byModule: s.byModule };
    });

  const resolved = wins + losses;
  return {
    suite: {
      net,
      trades,
      wins,
      losses,
      winRatePct: resolved > 0 ? (wins / resolved) * 100 : null,
      avg: trades > 0 ? net / trades : null,
    },
    daily,
    modules,
  };
}
