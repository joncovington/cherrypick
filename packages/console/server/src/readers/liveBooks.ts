import path from "node:path";
import type Database from "better-sqlite3";
import type { LiveBook, LiveBooksPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readOnlyDb } from "./db.js";

/**
 * Every module with a live book, and whether it has any live activity -- what the Overview's live
 * panel lists (2026-10-12; it had been the flies pilot alone).
 *
 * A mirror of each module's own settled-net query, named per module, never a derivation:
 * - **flies** `live_trades.db`: `fly_positions`, net = `gross_pnl - fees` over `status = 'settled'`
 *   by `trade_date` (core.ledgers' flies rule, the Live page's `settledNet`); open = `status = 'open'`.
 * - **bwb** `live_trades.db`: `bwb_positions`, net = `gross_pnl - fees` over `status = 'closed'` by
 *   `closed_session` (`db.settled_net_for_session`); open = neither closed nor cancelled. A
 *   cancelled entry never held risk and is not activity.
 *
 * `readOnlyDb`, so "no live ledger here" (`absent`) and "the read threw" (`failed`) stay apart.
 */

interface Spec {
  module: LiveBook["module"];
  dir: keyof ConsoleConfig["paths"];
  /** Where the module's own page shows its live book. */
  to: string;
  read: (db: Database.Database) => Omit<LiveBook, "module" | "ledger" | "error" | "to">;
}

function tableExists(db: Database.Database, name: string): boolean {
  return db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?").get(name) !== undefined;
}

const NONE = { positions: 0, open: 0, latestSession: null, latestNet: null, latestNetSession: null, totalNet: null };

export const LIVE_BOOKS: readonly Spec[] = [
  {
    module: "flies",
    dir: "fliesDir",
    to: "/live/flies",
    read: (db) => {
      if (!tableExists(db, "fly_positions")) return NONE;
      const counts = db
        .prepare(
          "SELECT COUNT(*) AS n, SUM(status = 'open') AS open, MAX(trade_date) AS latest FROM fly_positions " +
            "WHERE status != 'cancelled'",
        )
        .get() as { n: number; open: number | null; latest: string | null };
      const settled = db
        .prepare(
          "SELECT trade_date AS day, ROUND(SUM(COALESCE(gross_pnl, 0) - COALESCE(fees, 0)), 2) AS net " +
            "FROM fly_positions WHERE status = 'settled' GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1",
        )
        .get() as { day: string; net: number } | undefined;
      const total = db
        .prepare("SELECT ROUND(SUM(COALESCE(gross_pnl, 0) - COALESCE(fees, 0)), 2) AS net FROM fly_positions WHERE status = 'settled'")
        .get() as { net: number | null };
      return {
        positions: counts.n,
        open: counts.open ?? 0,
        latestSession: counts.latest,
        latestNet: settled?.net ?? null,
        latestNetSession: settled?.day ?? null,
        totalNet: total.net,
      };
    },
  },
  {
    module: "bwb",
    dir: "bwbDir",
    to: "/bwb/livegap",
    read: (db) => {
      if (!tableExists(db, "bwb_positions")) return NONE;
      const counts = db
        .prepare(
          "SELECT COUNT(*) AS n, SUM(status != 'closed') AS open, MAX(entry_session) AS latest FROM bwb_positions " +
            "WHERE status != 'cancelled'",
        )
        .get() as { n: number; open: number | null; latest: string | null };
      const settled = db
        .prepare(
          "SELECT closed_session AS day, ROUND(SUM(COALESCE(gross_pnl, 0) - COALESCE(fees, 0)), 2) AS net " +
            "FROM bwb_positions WHERE status = 'closed' AND closed_session IS NOT NULL " +
            "GROUP BY closed_session ORDER BY closed_session DESC LIMIT 1",
        )
        .get() as { day: string; net: number } | undefined;
      const total = db
        .prepare("SELECT ROUND(SUM(COALESCE(gross_pnl, 0) - COALESCE(fees, 0)), 2) AS net FROM bwb_positions WHERE status = 'closed'")
        .get() as { net: number | null };
      return {
        positions: counts.n,
        open: counts.open ?? 0,
        latestSession: counts.latest,
        latestNet: settled?.net ?? null,
        latestNetSession: settled?.day ?? null,
        totalNet: total.net,
      };
    },
  },
];

export function readLiveBooks(config: ConsoleConfig): LiveBooksPayload {
  const books: LiveBook[] = LIVE_BOOKS.map((spec) => {
    const file = path.join(config.paths[spec.dir] as string, "live_trades.db");
    const out = readOnlyDb(file, spec.read);
    if (out.status === "ok") return { module: spec.module, ledger: "ok", error: null, to: spec.to, ...out.value };
    return {
      module: spec.module,
      ledger: out.status,
      error: out.status === "failed" ? out.error : null,
      to: spec.to,
      ...NONE,
    };
  });
  return { books };
}
