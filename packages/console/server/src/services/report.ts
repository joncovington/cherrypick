/**
 * Port of the orchestrator report's cross-module paper P&L rollup — the
 * per-schema net rules are the load-bearing part and copied exactly:
 *   meic ic_trades:   net = pnl − fees (resolved trades)
 *   earnings trades:  net = pnl − entry_cost − exit_cost (closed)
 *   flies fly_books:  net = pnl (book P&L is already net of fees when settled)
 * Sessions keyed by trade date; suite stats = net, trades, wins/losses on the
 * per-trade net, win rate, average.
 */

import fs from "node:fs";
import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb } from "../readers/db.js";
import { listSessions } from "../readers/review.js";

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


interface FactModule {
  net: number;
  closed: number;
  wins: number;
  losses: number;
}

/** One session's per-module results, or null if the artifact is missing or unreadable. */
function readFactSet(config: ConsoleConfig, session: string): Record<string, FactModule> | null {
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(
      fs.readFileSync(path.join(config.paths.reviewDir, `eod-${session}.json`), "utf-8"),
    );
  } catch {
    return null;
  }
  const modules = parsed["modules"];
  if (!modules || typeof modules !== "object") return null;
  const out: Record<string, FactModule> = {};
  for (const [name, raw] of Object.entries(modules as Record<string, unknown>)) {
    const m = (raw ?? {}) as Record<string, unknown>;
    if (m["ok"] !== true) continue;
    const r = (m["results"] ?? {}) as Record<string, unknown>;
    const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : 0);
    out[name] = { net: n(r["net"]), closed: n(r["closed"]), wins: n(r["wins"]), losses: n(r["losses"]) };
  }
  return out;
}

export interface SuiteReport {
  suite: { net: number; trades: number; wins: number; losses: number; winRatePct: number | null; avg: number | null };
  /** Sessions ascending; cumulative suite equity plus per-module cumulative lines. */
  daily: Array<{ session: string; net: number; cumulative: number; byModule: Record<string, number> }>;
  modules: Record<string, { net: number; trades: number; wins: number; losses: number }>;
}

export function buildSuiteReport(config: ConsoleConfig): SuiteReport {
  // Reads the review's fact sets, NOT the ledgers.
  //
  // The per-schema net rules used to be implemented here, hand-copied from the orchestrator's
  // Python and flagged in this file's own docstring as "copied exactly" — and they had already
  // drifted: the orchestrator reads flies from `fly_positions`, this port read `fly_books`. Two
  // implementations of a rule that must not differ is one too many, so the rule now lives in
  // `cherrypick.core.ledgers`, `packages/review` applies it once per session, and this function
  // sums the resulting artifacts.
  //
  // The interface is unchanged, so nothing downstream had to move. What changed is depth: this is
  // now exactly the sessions that have been built (2026-07-10 onward), not everything the ledgers
  // hold. The review page states its own coverage; a caller wanting all-of-history from the raw
  // ledgers should ask the orchestrator's `report`, which is the surface that still does that.
  const sessions = new Map<string, { net: number; byModule: Record<string, number> }>();
  const modules: SuiteReport["modules"] = {};
  let net = 0;
  let trades = 0;
  let wins = 0;
  let losses = 0;

  for (const session of listSessions(config)) {
    const facts = readFactSet(config, session);
    if (facts === null) continue;
    for (const [mod, raw] of Object.entries(facts)) {
      const m = (modules[mod] ??= { net: 0, trades: 0, wins: 0, losses: 0 });
      m.net += raw.net;
      m.trades += raw.closed;
      m.wins += raw.wins;
      m.losses += raw.losses;
      net += raw.net;
      trades += raw.closed;
      wins += raw.wins;
      losses += raw.losses;

      let s = sessions.get(session);
      if (s === undefined) {
        s = { net: 0, byModule: {} };
        sessions.set(session, s);
      }
      s.net += raw.net;
      s.byModule[mod] = (s.byModule[mod] ?? 0) + raw.net;
    }
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
