import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../config.js";

/**
 * The console's ONLY writable store: ~/.cherrypick/data/console/console.db.
 * Every other database this package touches is opened read-only.
 *
 * It holds one table, `console_prefs`. The rest -- watchlists, staged tickets, candles, chain EOD,
 * broker metrics and their caches -- belonged to the research surfaces retired on 2026-08-31, and
 * were still created on every open and left holding ~60 MB (2.8M candle rows). They are dropped on
 * open, and the file vacuumed once when anything was dropped; a fresh machine never creates them.
 */
const RETIRED_TABLES = [
  "watchlist",
  "staged_orders",
  "candles",
  "candle_meta",
  "tt_watchlists",
  "tt_public_pins",
  "tt_metrics",
  "symbol_blacklist",
  "chain_eod",
  "chain_eod_meta",
];

let db: Database.Database | null = null;

function open(config: ConsoleConfig): Database.Database {
  if (db !== null) return db;
  fs.mkdirSync(config.paths.consoleData, { recursive: true });
  db = new Database(path.join(config.paths.consoleData, "console.db"));
  db.pragma("journal_mode = WAL");
  const present = new Set(
    db
      .prepare<[], { name: string }>("SELECT name FROM sqlite_master WHERE type = 'table'")
      .all()
      .map((r) => r.name),
  );
  const retired = RETIRED_TABLES.filter((t) => present.has(t));
  for (const t of retired) db.exec(`DROP TABLE ${t}`);
  if (retired.length > 0) db.exec("VACUUM");
  // In WAL mode a VACUUM's pages sit in the log until a checkpoint; truncating on open is what
  // actually returns the space to the disk (and keeps the log small after every restart).
  db.pragma("wal_checkpoint(TRUNCATE)");
  db.exec(`
    CREATE TABLE IF NOT EXISTS console_prefs (
      key        TEXT PRIMARY KEY,
      value_json TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
  `);
  return db;
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
