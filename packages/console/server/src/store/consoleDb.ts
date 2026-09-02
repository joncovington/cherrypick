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
    -- The six tables below belonged to the research/screener section retired on 2026-08-31.
    -- Nothing reads or writes them any more; the DDL and the rows are kept deliberately for one
    -- cycle so the retirement is reversible without a restore, and so a console pointed at an
    -- existing store does not error on a schema that suddenly lost tables. Drop them in a
    -- follow-up once the section is confirmed unwanted.
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
      dividend_ex_date TEXT,
      dividend_next_date TEXT,
      dividend_rate REAL,
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
    CREATE TABLE IF NOT EXISTS console_prefs (
      key        TEXT PRIMARY KEY,
      value_json TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
  `);
  addColumns("tt_metrics", [
    "liquidity REAL",
    "pe REAL",
    "div_yield REAL",
    "earnings_date TEXT",
    // The dividend dates exist so `narrative.eventWarnings` can fire its ex-dividend warning. It
    // was reachable only through a null and therefore never fired, which in that function is not a
    // quiet no-op: absence of a warning is a real claim there, so a short ITM call over an ex-date
    // read as "nothing to flag". The metrics response already carried these fields.
    "dividend_ex_date TEXT",
    "dividend_next_date TEXT",
    "dividend_rate REAL",
  ]);
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

// ---- Console-owned daily candle cache (filled by DXLink backfill only) ----

export interface CandleBar {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
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
  dividendExDate: string | null; // most recent/next ex-dividend date, ISO
  dividendNextDate: string | null; // next scheduled ex-dividend date, ISO
  dividendRate: number | null; // dollars per share
  updatedAt: number;
}

// ---- Symbol blacklist (learned, e.g. "no weekly options"; user-clearable) ----

export interface BlacklistRow {
  symbol: string;
  reason: string;
  addedAt: string;
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

/**
 * The console's own preferences — display choices that belong to this UI and to nothing else.
 * Deliberately separate from the suite config the Config page edits through the orchestrator's
 * editor: these have no blast radius beyond a browser, so they save on change rather than through
 * a staged section save.
 */
export function getPrefs(config: ConsoleConfig): Record<string, unknown> {
  const rows = open(config)
    .prepare<[], { key: string; value_json: string }>("SELECT key, value_json FROM console_prefs")
    .all();
  const out: Record<string, unknown> = {};
  for (const r of rows) {
    try {
      out[r.key] = JSON.parse(r.value_json);
    } catch {
      /* a value we can't parse is a value we don't have */
    }
  }
  return out;
}

export function setPref(config: ConsoleConfig, key: string, value: unknown): void {
  open(config)
    .prepare(
      `INSERT INTO console_prefs (key, value_json, updated_at) VALUES (?, ?, ?)
         ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at`,
    )
    .run(key, JSON.stringify(value ?? null), new Date().toISOString());
}
