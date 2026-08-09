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
  `);
  return db;
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
