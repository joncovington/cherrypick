import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { hasTable, type DatabaseHandle, withReadOnlyDb } from "./db.js";

/**
 * cherrypick-ladder's paper ledger (`packages/ladder`), read only. The module has no page yet; this
 * reader gives the shared decisions card its session, the loop's last run, so `ladder_decisions` is
 * read from the day it ships rather than written into a table nothing shows.
 */

const DB_FILE = "paper_trades.db";

export function ladderDbPath(config: ConsoleConfig): string {
  return path.join(config.paths.ladderDir, DB_FILE);
}

function resolveFrom(db: DatabaseHandle): string | null {
  if (!hasTable(db, "ladder_loop_iterations")) return null;
  return db.prepare<[], { d: string | null }>("SELECT MAX(session_date) AS d FROM ladder_loop_iterations").get()?.d ?? null;
}

/** The session the ladder's cards name: the loop's last run. */
export function resolveLadderSession(config: ConsoleConfig): string | null {
  return withReadOnlyDb<string | null>(ladderDbPath(config), null, resolveFrom);
}
