import path from "node:path";
import type { TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, num, str, hasColumn } from "./db.js";

/**
 * The entry-attempts ledger — one row per evaluated entry opportunity per arm.
 *
 * This is the read side of the measurement both modules started keeping on
 * 2026-08-11, when each arm became an independent portfolio with unbounded
 * capital. Every arm now sees the same market with the same money, so the only
 * thing that differentiates them is which entries the rules let through: the
 * refusals are the primary signal, not a diagnostic.
 *
 * MEIC and flies keep separate tables with the same shape (`entry_attempts`
 * and `fly_entry_attempts`), deliberately — each lives in its own module's
 * ledger and neither module may read the other's — so this normalizes the two
 * into one row type for the UI rather than making the pages know the
 * difference.
 */

/** The outcome taxonomy both modules write. `noFill` is deliberately distinct
 *  from a gate refusal: under a fill-based cadence clock an entry that cleared
 *  every gate and simply did not fill neither spent the arm's slot nor was
 *  refused by a rule, and folding the two together makes the gates look
 *  stricter than they are. */
export type AttemptOutcome =
  | "filled"
  | "cadence_blocked"
  | "sign_rule_blocked"
  | "duplicate_blocked"
  | "gate_blocked"
  | "window_blocked"
  | "no_candidate"
  | "no_fill";

export interface AttemptRow {
  ts: string | null;
  tradeDate: string | null;
  arm: string;
  symbol: string | null;
  outcome: string;
  blockDetail: string | null;
  center: number | null;
  blockingStrike: number | null;
  secondsUntilCadenceClear: number | null;
  spot: number | null;
}

export interface ArmRailEntry {
  arm: string;
  attempts: number;
  fills: number;
  /** Refusals by outcome — the shape the rail's "why is this arm quiet" read needs. */
  refusals: Record<string, number>;
  /** The most recent refusal reason, or null if the last thing it did was fill. */
  lastRefusal: string | null;
  lastAttemptTs: string | null;
  lastFillTs: string | null;
}

export interface AttemptsPayload {
  mode: TradingMode;
  module: "meic" | "flies";
  tradeDate: string | null;
  arms: ArmRailEntry[];
  /** Every attempt for the day, oldest first — the attempt timeline's source. */
  timeline: AttemptRow[];
}

interface TableSpec {
  file: (mode: TradingMode) => string;
  dir: (config: ConsoleConfig) => string;
  table: string;
  armColumn: string;
  centerColumn: string;
}

const SPECS: Record<"meic" | "flies", TableSpec> = {
  meic: {
    file: (mode) => (mode === "live" ? "meic_trades.db" : "paper_trades.db"),
    dir: (config) => config.paths.meicDir,
    table: "entry_attempts",
    armColumn: "risk_profile",
    // MEIC has no single "centre" — the profit zone is the short PAIR — so the
    // put short stands in for one on the timeline, and the pair is available in
    // the row itself for anything that needs both.
    centerColumn: "put_strike",
  },
  flies: {
    file: (mode) => (mode === "live" ? "live_trades.db" : "paper_trades.db"),
    dir: (config) => config.paths.fliesDir,
    table: "fly_entry_attempts",
    armColumn: "arm",
    centerColumn: "center",
  },
};

/**
 * The arm rail and the attempt timeline for one module on one day.
 *
 * Returns an empty payload rather than throwing when the table is absent: a
 * ledger written by a checkout that predates the attempts work is a legitimate
 * state, and the console is read-only over every other package's data — it must
 * degrade to "nothing recorded" rather than fail the page.
 */
export function readEntryAttempts(
  config: ConsoleConfig,
  module: "meic" | "flies",
  mode: TradingMode,
  day: string | null,
): AttemptsPayload {
  const spec = SPECS[module];
  const dbPath = path.join(spec.dir(config), spec.file(mode));
  const empty: AttemptsPayload = { mode, module, tradeDate: null, arms: [], timeline: [] };

  return withReadOnlyDb<AttemptsPayload>(dbPath, empty, (db) => {
    if (!hasColumn(db, spec.table, "outcome")) return empty;

    const dayRow = day
      ? { d: day }
      : db.prepare<[], { d: string }>(`SELECT MAX(trade_date) AS d FROM ${spec.table}`).get();
    const tradeDate = dayRow?.d ?? null;
    if (tradeDate === null) return empty;

    const rows = db
      .prepare<[string], Record<string, unknown>>(
        `SELECT ts, trade_date, ${spec.armColumn} AS arm, symbol, outcome, block_detail,
                ${spec.centerColumn} AS center, blocking_strike, seconds_until_cadence_clear,
                ${module === "meic" ? "underlying_price" : "spot"} AS spot
           FROM ${spec.table}
          WHERE trade_date = ?
          ORDER BY ts ASC, id ASC`,
      )
      .all(tradeDate);

    const timeline: AttemptRow[] = rows.map((r) => ({
      ts: str(r["ts"]),
      tradeDate: str(r["trade_date"]),
      arm: str(r["arm"]) ?? "?",
      symbol: str(r["symbol"]),
      outcome: str(r["outcome"]) ?? "gate_blocked",
      blockDetail: str(r["block_detail"]),
      center: num(r["center"]),
      blockingStrike: num(r["blocking_strike"]),
      secondsUntilCadenceClear: num(r["seconds_until_cadence_clear"]),
      spot: num(r["spot"]),
    }));

    const byArm = new Map<string, ArmRailEntry>();
    for (const row of timeline) {
      let entry = byArm.get(row.arm);
      if (entry === undefined) {
        entry = {
          arm: row.arm,
          attempts: 0,
          fills: 0,
          refusals: {},
          lastRefusal: null,
          lastAttemptTs: null,
          lastFillTs: null,
        };
        byArm.set(row.arm, entry);
      }
      entry.attempts += 1;
      entry.lastAttemptTs = row.ts;
      if (row.outcome === "filled") {
        entry.fills += 1;
        entry.lastFillTs = row.ts;
        // Cleared on a fill so the rail reads "what is holding this arm back
        // RIGHT NOW" rather than surfacing a refusal the arm has since moved past.
        entry.lastRefusal = null;
      } else {
        entry.refusals[row.outcome] = (entry.refusals[row.outcome] ?? 0) + 1;
        entry.lastRefusal = row.blockDetail ?? row.outcome;
      }
    }

    return {
      mode,
      module,
      tradeDate,
      arms: [...byArm.values()].sort((a, b) => a.arm.localeCompare(b.arm)),
      timeline,
    };
  });
}
