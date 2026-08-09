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
  `);
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
