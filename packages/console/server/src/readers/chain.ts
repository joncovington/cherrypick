import fs from "node:fs";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../config.js";

export interface ChainSide {
  streamerSymbol: string;
  /** OCC symbol (e.g. "SPXW  260810P07700000") — what an order ticket needs. */
  occSymbol: string | null;
  bid: number | null;
  ask: number | null;
  delta: number | null;
  iv: number | null;
  openInterest: number | null;
  /** Seconds since the quote row was last written, so stale strikes read as stale. */
  quoteAge: number | null;
}

export interface ChainRow {
  strike: number;
  call: ChainSide | null;
  put: ChainSide | null;
}

export interface ChainPayload {
  symbol: string;
  expiration: string | null;
  expirations: string[];
  rows: ChainRow[];
}

/**
 * Option chain assembled entirely from the shared stream cache (read-only):
 * stream_chain for the strikes, stream_quotes/stream_greeks/stream_oi joined
 * by streamer symbol. No broker call on this path — streamer before API.
 * Coverage note: the streamer only promises a spot + ATM window per requested
 * underlying, so far-from-the-money strikes may be quote-less; they still
 * render (with delta/OI when known) rather than disappearing.
 */
export function readChain(config: ConsoleConfig, symbol: string, expiration: string | null): ChainPayload {
  const p = config.paths.streamCacheDb;
  if (!fs.existsSync(p)) return { symbol, expiration, expirations: [], rows: [] };
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");

    const expByQuotes = db
      .prepare<[string], { expiration: string; quoted: number }>(
        `SELECT c.expiration, COUNT(q.symbol) AS quoted
           FROM stream_chain c LEFT JOIN stream_quotes q ON q.symbol = c.streamer_symbol
          WHERE c.underlying_symbol = ?
          GROUP BY c.expiration ORDER BY c.expiration`,
      )
      .all(symbol);
    const expirations = expByQuotes.map((r) => r.expiration);
    // Default to the latest expiration that actually has cached quotes (e.g.
    // Friday's session data on a weekend), not just the latest expiration.
    const latestQuoted = [...expByQuotes].reverse().find((r) => r.quoted > 0)?.expiration ?? null;
    const exp = expiration ?? latestQuoted ?? expirations[expirations.length - 1] ?? null;
    if (exp === null) return { symbol, expiration: null, expirations, rows: [] };

    const now = Date.now() / 1000;
    const chainRows = db
      .prepare<[string, string], { streamer_symbol: string; data_json: string }>(
        "SELECT streamer_symbol, data_json FROM stream_chain WHERE underlying_symbol = ? AND expiration = ?",
      )
      .all(symbol, exp);

    const quoteStmt = db.prepare<[string], Record<string, unknown>>(
      "SELECT bid, ask, updated_at FROM stream_quotes WHERE symbol = ?",
    );
    const greeksStmt = db.prepare<[string], Record<string, unknown>>(
      "SELECT delta, iv FROM stream_greeks WHERE symbol = ?",
    );
    const oiStmt = db.prepare<[string], Record<string, unknown>>(
      "SELECT open_interest FROM stream_oi WHERE symbol = ?",
    );
    const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

    const byStrike = new Map<number, ChainRow>();
    for (const row of chainRows) {
      let meta: Record<string, unknown>;
      try {
        meta = JSON.parse(row.data_json) as Record<string, unknown>;
      } catch {
        continue;
      }
      const strike = Number(meta["strike_price"]);
      const optType = meta["option_type"];
      if (!Number.isFinite(strike) || (optType !== "C" && optType !== "P")) continue;

      const q = quoteStmt.get(row.streamer_symbol);
      const g = greeksStmt.get(row.streamer_symbol);
      const oi = oiStmt.get(row.streamer_symbol);
      const updatedAt = num(q?.["updated_at"]);
      const side: ChainSide = {
        streamerSymbol: row.streamer_symbol,
        occSymbol: typeof meta["symbol"] === "string" ? meta["symbol"] : null,
        bid: num(q?.["bid"]),
        ask: num(q?.["ask"]),
        delta: num(g?.["delta"]),
        iv: num(g?.["iv"]),
        openInterest: num(oi?.["open_interest"]),
        quoteAge: updatedAt !== null ? Math.max(0, now - updatedAt) : null,
      };

      let entry = byStrike.get(strike);
      if (entry === undefined) {
        entry = { strike, call: null, put: null };
        byStrike.set(strike, entry);
      }
      if (optType === "C") entry.call = side;
      else entry.put = side;
    }

    const rows = [...byStrike.values()].sort((a, b) => a.strike - b.strike);
    return { symbol, expiration: exp, expirations, rows };
  } catch {
    return { symbol, expiration, expirations: [], rows: [] };
  } finally {
    db?.close();
  }
}
