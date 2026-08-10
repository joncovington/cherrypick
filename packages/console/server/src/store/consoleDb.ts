import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../config.js";

/**
 * The console's ONLY writable store: ~/.cherrypick/data/console/console.db.
 * Every other database this package touches is opened read-only.
 */
let db: Database.Database | null = null;

function open(config: ConsoleConfig): Database.Database {
  if (db !== null) return db;
  fs.mkdirSync(config.paths.consoleData, { recursive: true });
  db = new Database(path.join(config.paths.consoleData, "console.db"));
  db.pragma("journal_mode = WAL");
  // Additive migration for pre-existing tables (CREATE IF NOT EXISTS won't
  // add columns): ALTERs are best-effort, erroring only when already applied.
  const addColumns = (table: string, cols: string[]): void => {
    for (const col of cols) {
      try {
        db!.exec(`ALTER TABLE ${table} ADD COLUMN ${col}`);
      } catch {
        /* column exists or table not created yet — the DDL below covers it */
      }
    }
  };
  db.exec(`
    CREATE TABLE IF NOT EXISTS watchlist (
      symbol   TEXT PRIMARY KEY,
      added_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS staged_orders (
      id           TEXT PRIMARY KEY,
      created_at   TEXT NOT NULL,
      symbol       TEXT NOT NULL,
      strategy     TEXT,
      legs_json    TEXT NOT NULL,
      credit       REAL,
      max_risk     REAL,
      dry_run_json TEXT,
      note         TEXT,
      status       TEXT NOT NULL DEFAULT 'staged'
    );
    CREATE TABLE IF NOT EXISTS candles (
      symbol TEXT NOT NULL, ts INTEGER NOT NULL,
      o REAL, h REAL, l REAL, c REAL, v REAL,
      PRIMARY KEY (symbol, ts)
    );
    CREATE TABLE IF NOT EXISTS candle_meta (
      symbol TEXT PRIMARY KEY, last_backfill REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tt_watchlists (
      key          TEXT PRIMARY KEY,
      kind         TEXT NOT NULL,
      name         TEXT NOT NULL,
      symbols_json TEXT NOT NULL,
      skipped_json TEXT NOT NULL DEFAULT '[]',
      fetched_at   REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tt_public_pins (
      name TEXT PRIMARY KEY
    );
    CREATE TABLE IF NOT EXISTS tt_metrics (
      symbol     TEXT PRIMARY KEY,
      iv_rank    REAL,
      iv_index   REAL,
      market_cap REAL,
      liquidity  REAL,
      pe         REAL,
      div_yield  REAL,
      earnings_date TEXT,
      updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS symbol_blacklist (
      symbol   TEXT PRIMARY KEY,
      reason   TEXT NOT NULL,
      added_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS chain_eod (
      trade_date TEXT NOT NULL,
      symbol     TEXT NOT NULL,
      expiration TEXT NOT NULL,
      strike     REAL NOT NULL,
      otype      TEXT NOT NULL,
      bid REAL, ask REAL, mid REAL, delta REAL, iv REAL,
      PRIMARY KEY (trade_date, symbol, expiration, strike, otype)
    );
    CREATE TABLE IF NOT EXISTS chain_eod_meta (
      trade_date  TEXT NOT NULL,
      symbol      TEXT NOT NULL,
      spot        REAL NOT NULL,
      captured_at TEXT NOT NULL,
      PRIMARY KEY (trade_date, symbol)
    );
  `);
  addColumns("tt_metrics", ["liquidity REAL", "pe REAL", "div_yield REAL", "earnings_date TEXT"]);
  return db;
}

export interface StagedTicket {
  id: string;
  createdAt: string;
  symbol: string;
  strategy: string | null;
  legs: unknown;
  credit: number | null;
  maxRisk: number | null;
  dryRun: unknown;
  note: string | null;
  status: string;
}

function rowToTicket(r: Record<string, unknown>): StagedTicket {
  return {
    id: String(r["id"]),
    createdAt: String(r["created_at"]),
    symbol: String(r["symbol"]),
    strategy: r["strategy"] === null ? null : String(r["strategy"]),
    legs: JSON.parse(String(r["legs_json"])),
    credit: typeof r["credit"] === "number" ? r["credit"] : null,
    maxRisk: typeof r["max_risk"] === "number" ? r["max_risk"] : null,
    dryRun: r["dry_run_json"] === null ? null : JSON.parse(String(r["dry_run_json"])),
    note: r["note"] === null ? null : String(r["note"]),
    status: String(r["status"]),
  };
}

export function listStaged(config: ConsoleConfig): StagedTicket[] {
  return open(config)
    .prepare<[], Record<string, unknown>>("SELECT * FROM staged_orders ORDER BY created_at DESC")
    .all()
    .map(rowToTicket);
}

export function insertStaged(
  config: ConsoleConfig,
  ticket: {
    id: string;
    symbol: string;
    strategy: string | null;
    legs: unknown;
    credit: number | null;
    maxRisk: number | null;
    dryRun: unknown;
    note: string | null;
  },
): StagedTicket {
  const createdAt = new Date().toISOString();
  open(config)
    .prepare(
      `INSERT INTO staged_orders (id, created_at, symbol, strategy, legs_json, credit, max_risk, dry_run_json, note, status)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged')`,
    )
    .run(
      ticket.id,
      createdAt,
      ticket.symbol,
      ticket.strategy,
      JSON.stringify(ticket.legs),
      ticket.credit,
      ticket.maxRisk,
      JSON.stringify(ticket.dryRun),
      ticket.note,
    );
  return {
    ...ticket,
    createdAt,
    status: "staged",
  } as StagedTicket;
}

export function deleteStaged(config: ConsoleConfig, id: string): boolean {
  return open(config).prepare("DELETE FROM staged_orders WHERE id = ?").run(id).changes > 0;
}

export function getWatchlist(config: ConsoleConfig): string[] {
  return open(config)
    .prepare<[], { symbol: string }>("SELECT symbol FROM watchlist ORDER BY symbol")
    .all()
    .map((r) => r.symbol);
}

export function addToWatchlist(config: ConsoleConfig, symbol: string): void {
  open(config)
    .prepare("INSERT OR IGNORE INTO watchlist (symbol, added_at) VALUES (?, ?)")
    .run(symbol, new Date().toISOString());
}

export function removeFromWatchlist(config: ConsoleConfig, symbol: string): boolean {
  return open(config).prepare("DELETE FROM watchlist WHERE symbol = ?").run(symbol).changes > 0;
}

// ---- Console-owned daily candle cache (filled by DXLink backfill only) ----

export interface CandleBar {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export function readOwnCandles(config: ConsoleConfig, symbol: string): CandleBar[] {
  return open(config)
    .prepare<[string], Record<string, unknown>>(
      "SELECT ts, o, h, l, c, v FROM candles WHERE symbol = ? ORDER BY ts",
    )
    .all(symbol)
    .map((r) => ({
      t: Number(r["ts"]),
      o: Number(r["o"]),
      h: Number(r["h"]),
      l: Number(r["l"]),
      c: Number(r["c"]),
      v: Number(r["v"]),
    }));
}

export function writeOwnCandles(config: ConsoleConfig, symbol: string, bars: CandleBar[]): void {
  const db = open(config);
  const insert = db.prepare(
    "INSERT OR REPLACE INTO candles (symbol, ts, o, h, l, c, v) VALUES (?, ?, ?, ?, ?, ?, ?)",
  );
  const tx = db.transaction((rows: CandleBar[]) => {
    for (const b of rows) insert.run(symbol, b.t, b.o, b.h, b.l, b.c, b.v);
  });
  tx(bars);
  db.prepare("INSERT OR REPLACE INTO candle_meta (symbol, last_backfill) VALUES (?, ?)").run(
    symbol,
    Date.now() / 1000,
  );
}

export function candleLastBackfill(config: ConsoleConfig, symbol: string): number | null {
  const row = open(config)
    .prepare<[string], { last_backfill: number }>(
      "SELECT last_backfill FROM candle_meta WHERE symbol = ?",
    )
    .get(symbol);
  return row?.last_backfill ?? null;
}

export function candleCount(config: ConsoleConfig, symbol: string): number {
  const row = open(config)
    .prepare<[string], { n: number }>("SELECT COUNT(*) AS n FROM candles WHERE symbol = ?")
    .get(symbol);
  return row?.n ?? 0;
}

// ---- Cached market metrics (IV rank/index, market cap; one batched call, TTL'd) ----

export interface TtMetricsRow {
  symbol: string;
  ivRank: number | null; // 0..1 fraction as the API returns it
  ivIndex: number | null; // fraction, e.g. 0.431
  marketCap: number | null;
  liquidity: number | null; // 0..4 rating
  pe: number | null;
  divYield: number | null; // fraction
  earningsDate: string | null; // next expected report date, ISO
  updatedAt: number;
}

export function readTtMetrics(config: ConsoleConfig, symbols: string[]): Map<string, TtMetricsRow> {
  const out = new Map<string, TtMetricsRow>();
  if (symbols.length === 0) return out;
  const stmt = open(config).prepare<[string], Record<string, unknown>>(
    "SELECT * FROM tt_metrics WHERE symbol = ?",
  );
  for (const s of symbols) {
    const r = stmt.get(s);
    if (r === undefined) continue;
    out.set(s, {
      symbol: s,
      ivRank: typeof r["iv_rank"] === "number" ? r["iv_rank"] : null,
      ivIndex: typeof r["iv_index"] === "number" ? r["iv_index"] : null,
      marketCap: typeof r["market_cap"] === "number" ? r["market_cap"] : null,
      liquidity: typeof r["liquidity"] === "number" ? r["liquidity"] : null,
      pe: typeof r["pe"] === "number" ? r["pe"] : null,
      divYield: typeof r["div_yield"] === "number" ? r["div_yield"] : null,
      earningsDate: typeof r["earnings_date"] === "string" ? r["earnings_date"] : null,
      updatedAt: Number(r["updated_at"]),
    });
  }
  return out;
}

export function writeTtMetrics(
  config: ConsoleConfig,
  rows: Array<Omit<TtMetricsRow, "updatedAt">>,
  now: number,
): void {
  const db = open(config);
  const insert = db.prepare(
    `INSERT OR REPLACE INTO tt_metrics (symbol, iv_rank, iv_index, market_cap, liquidity, pe, div_yield, earnings_date, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  const tx = db.transaction((all: typeof rows) => {
    for (const r of all) {
      insert.run(r.symbol, r.ivRank, r.ivIndex, r.marketCap, r.liquidity, r.pe, r.divYield, r.earningsDate, now);
    }
  });
  tx(rows);
}

// ---- Symbol blacklist (learned, e.g. "no weekly options"; user-clearable) ----

export interface BlacklistRow {
  symbol: string;
  reason: string;
  addedAt: string;
}

export function listBlacklist(config: ConsoleConfig): BlacklistRow[] {
  return open(config)
    .prepare<[], Record<string, unknown>>("SELECT * FROM symbol_blacklist ORDER BY symbol")
    .all()
    .map((r) => ({ symbol: String(r["symbol"]), reason: String(r["reason"]), addedAt: String(r["added_at"]) }));
}

export function getBlacklistReason(config: ConsoleConfig, symbol: string): string | null {
  const r = open(config)
    .prepare<[string], { reason: string }>("SELECT reason FROM symbol_blacklist WHERE symbol = ?")
    .get(symbol);
  return r?.reason ?? null;
}

export function addToBlacklist(config: ConsoleConfig, symbol: string, reason: string): void {
  open(config)
    .prepare("INSERT OR REPLACE INTO symbol_blacklist (symbol, reason, added_at) VALUES (?, ?, ?)")
    .run(symbol, reason, new Date().toISOString());
}

export function removeFromBlacklist(config: ConsoleConfig, symbol: string): boolean {
  return open(config).prepare("DELETE FROM symbol_blacklist WHERE symbol = ?").run(symbol).changes > 0;
}

// ---- EOD chain snapshots (once daily near the close, console's own session) ----

export interface ChainEodOptionRow {
  expiration: string;
  strike: number;
  otype: "C" | "P";
  bid: number | null;
  ask: number | null;
  mid: number | null;
  delta: number | null;
  iv: number | null;
}

export function writeChainEod(
  config: ConsoleConfig,
  tradeDate: string,
  symbol: string,
  spot: number,
  rows: ChainEodOptionRow[],
): void {
  const db = open(config);
  const insert = db.prepare(
    `INSERT OR REPLACE INTO chain_eod (trade_date, symbol, expiration, strike, otype, bid, ask, mid, delta, iv)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  const tx = db.transaction((all: ChainEodOptionRow[]) => {
    db.prepare("DELETE FROM chain_eod WHERE trade_date = ? AND symbol = ?").run(tradeDate, symbol);
    for (const r of all) {
      insert.run(tradeDate, symbol, r.expiration, r.strike, r.otype, r.bid, r.ask, r.mid, r.delta, r.iv);
    }
    db.prepare(
      "INSERT OR REPLACE INTO chain_eod_meta (trade_date, symbol, spot, captured_at) VALUES (?, ?, ?, ?)",
    ).run(tradeDate, symbol, spot, new Date().toISOString());
  });
  tx(rows);
}

export function chainEodMeta(
  config: ConsoleConfig,
  tradeDate: string,
  symbol: string,
): { spot: number; capturedAt: string } | null {
  const r = open(config)
    .prepare<[string, string], Record<string, unknown>>(
      "SELECT spot, captured_at FROM chain_eod_meta WHERE trade_date = ? AND symbol = ?",
    )
    .get(tradeDate, symbol);
  return r === undefined ? null : { spot: Number(r["spot"]), capturedAt: String(r["captured_at"]) };
}

export function readChainEod(
  config: ConsoleConfig,
  tradeDate: string,
  symbol: string,
): ChainEodOptionRow[] {
  return open(config)
    .prepare<[string, string], Record<string, unknown>>(
      "SELECT expiration, strike, otype, bid, ask, mid, delta, iv FROM chain_eod WHERE trade_date = ? AND symbol = ? ORDER BY expiration, strike",
    )
    .all(tradeDate, symbol)
    .map((r) => ({
      expiration: String(r["expiration"]),
      strike: Number(r["strike"]),
      otype: r["otype"] === "P" ? ("P" as const) : ("C" as const),
      bid: typeof r["bid"] === "number" ? r["bid"] : null,
      ask: typeof r["ask"] === "number" ? r["ask"] : null,
      mid: typeof r["mid"] === "number" ? r["mid"] : null,
      delta: typeof r["delta"] === "number" ? r["delta"] : null,
      iv: typeof r["iv"] === "number" ? r["iv"] : null,
    }));
}

/** Latest snapshot date on file, with per-date symbol coverage. */
export function chainEodStatus(config: ConsoleConfig): { tradeDate: string; symbols: number } | null {
  const r = open(config)
    .prepare<[], Record<string, unknown>>(
      "SELECT trade_date, COUNT(*) AS n FROM chain_eod_meta GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1",
    )
    .get();
  return r === undefined ? null : { tradeDate: String(r["trade_date"]), symbols: Number(r["n"]) };
}

// ---- Cached tastytrade watchlists (read-only mirrors of the broker's lists) ----

export interface TtWatchlistCacheRow {
  key: string;
  kind: "user" | "public";
  name: string;
  symbols: string[];
  skipped: string[];
  fetchedAt: number;
}

function rowToTtWatchlist(r: Record<string, unknown>): TtWatchlistCacheRow {
  return {
    key: String(r["key"]),
    kind: r["kind"] === "public" ? "public" : "user",
    name: String(r["name"]),
    symbols: JSON.parse(String(r["symbols_json"])) as string[],
    skipped: JSON.parse(String(r["skipped_json"] ?? "[]")) as string[],
    fetchedAt: Number(r["fetched_at"]),
  };
}

export function upsertTtWatchlist(
  config: ConsoleConfig,
  row: {
    key: string;
    kind: "user" | "public";
    name: string;
    symbols: string[];
    skipped: string[];
    fetchedAt: number;
  },
): void {
  open(config)
    .prepare(
      `INSERT OR REPLACE INTO tt_watchlists (key, kind, name, symbols_json, skipped_json, fetched_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
    )
    .run(row.key, row.kind, row.name, JSON.stringify(row.symbols), JSON.stringify(row.skipped), row.fetchedAt);
}

export function getTtWatchlist(config: ConsoleConfig, key: string): TtWatchlistCacheRow | null {
  const r = open(config)
    .prepare<[string], Record<string, unknown>>("SELECT * FROM tt_watchlists WHERE key = ?")
    .get(key);
  return r === undefined ? null : rowToTtWatchlist(r);
}

export function listTtWatchlists(config: ConsoleConfig): TtWatchlistCacheRow[] {
  return open(config)
    .prepare<[], Record<string, unknown>>("SELECT * FROM tt_watchlists ORDER BY kind, name")
    .all()
    .map(rowToTtWatchlist);
}

export function deleteTtWatchlist(config: ConsoleConfig, key: string): void {
  open(config).prepare("DELETE FROM tt_watchlists WHERE key = ?").run(key);
}

export function listPublicPins(config: ConsoleConfig): string[] {
  return open(config)
    .prepare<[], { name: string }>("SELECT name FROM tt_public_pins ORDER BY name")
    .all()
    .map((r) => r.name);
}

export function setPublicPin(config: ConsoleConfig, name: string, pinned: boolean): void {
  if (pinned) {
    open(config).prepare("INSERT OR IGNORE INTO tt_public_pins (name) VALUES (?)").run(name);
  } else {
    open(config).prepare("DELETE FROM tt_public_pins WHERE name = ?").run(name);
  }
}

/** One-way seed from scout's watchlist.json (read-only on scout's side). */
export function importScoutWatchlist(config: ConsoleConfig): { imported: number; symbols: string[] } {
  const p = path.join(config.paths.scoutDir, "watchlist.json");
  let symbols: string[] = [];
  try {
    const raw = JSON.parse(fs.readFileSync(p, "utf-8")) as { symbols?: unknown };
    if (Array.isArray(raw.symbols)) symbols = raw.symbols.filter((s): s is string => typeof s === "string");
  } catch {
    return { imported: 0, symbols: [] };
  }
  let imported = 0;
  for (const s of symbols) {
    const before = open(config).prepare("SELECT 1 FROM watchlist WHERE symbol = ?").get(s);
    addToWatchlist(config, s);
    if (before === undefined) imported += 1;
  }
  return { imported, symbols };
}
