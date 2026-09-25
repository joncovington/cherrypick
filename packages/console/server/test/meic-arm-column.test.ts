import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { ARM_COLUMNS, closePooledDbs } from "../src/readers/db.js";
import {
  readMeicScope,
  readMeicPerformance,
  readMeicAnalytics,
  readMeicForest,
  readMeicDivergence,
  NO_SCOPE,
} from "../src/readers/meic.js";
import { readOccupancy } from "../src/readers/occupancy.js";
import { readEntryAttempts } from "../src/readers/attempts.js";
import { readExitReasons } from "../src/readers/exitReasons.js";
import { readMeicProfileGuide } from "../src/readers/experimentGuide.js";

/**
 * Every console reader of a meic ledger, run against the same rows under each name the arm column
 * has had. meic's column is `risk_profile` until its own rename window and `arm` after it, and the
 * console runs across that window: it must read either, on the paper and live ledgers alike.
 *
 * What this guards is the defect the 2026-09-23 `book` rename shipped. Four readers still named the
 * old column; `withReadOnlyDb` turned each "no such column" into its empty payload; the pages read
 * "nothing today" with HTTP 200. So an empty result is the FAILURE here, not a pass: every reader
 * must return the arm by name, and return the same thing under every spelling.
 *
 * The schema is meic's own (`fixtures/meic-ledger-schema.sql`, generated from `cmd_init_db` and
 * pinned by meic's test_console_schema_fixture.py), not a hand-kept copy that could drift from it.
 */

const ARM = "ctl-arm"; // distinctive, so finding it in a payload cannot be a coincidence
const DAY = "2026-09-24";
const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "meic-ledger-schema.sql"), "utf-8");

function tmpConfig(tmp: string): ConsoleConfig {
  return {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: "",
      watchdogLast: "",
      orchestratorConfig: "",
      consoleData: "",
      meicDir: path.join(tmp, "meic"),
      fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"),
      calendarsDir: path.join(tmp, "calendars"),
      pmccDir: path.join(tmp, "pmcc"),
      curveDir: path.join(tmp, "curve"),
      bwbDir: path.join(tmp, "bwb"),
      gexDir: "",
      reviewDir: "",
      overviewDir: "",
      advisorDir: "",
      adviceDir: "",
      meicRiskConfig: "",
      fliesConfig: "",
      pmccConfigCandidates: [],
      calendarsConfigCandidates: [],
      curveConfigCandidates: [],
    },
  } as unknown as ConsoleConfig;
}

/** meic's schema with its arm column spelled `spelling`, one settled and one open condor on DAY. */
function ledger(spelling: string): ConsoleConfig {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), `console-meic-${spelling}-`));
  fs.mkdirSync(path.join(tmp, "meic"));
  const db = new Database(path.join(tmp, "meic", "paper_trades.db"));
  db.exec(SCHEMA);
  for (const table of ["ic_trades", "entry_attempts"]) {
    const have = (db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>).map((c) => c.name);
    const current = ARM_COLUMNS.find((c) => have.includes(c));
    if (current === undefined) throw new Error(`fixture ${table} has no arm column`);
    if (current !== spelling) db.exec(`ALTER TABLE ${table} RENAME COLUMN ${current} TO ${spelling}`);
  }
  const now = `${DAY} 10:00:00-04:00`;
  const trade = db.prepare(
    `INSERT INTO ic_trades (trade_date, symbol, ic_order_id, status, ${spelling}, era, put_strike, call_strike,
       wing_width, net_credit, quantity, pnl, fees, entry_time, exit_time, exit_reason, underlying_price_entry,
       created_at, updated_at)
     VALUES (?, 'SPX', ?, ?, ?, 'advisor', ?, ?, 5, 1.2, 1, ?, ?, ?, ?, ?, 6000, ?, ?)`,
  );
  trade.run(DAY, "ic-1", "expired", ARM, 5950, 6050, 120, 4.5, now, `${DAY} 16:00:00-04:00`, "expired", now, now);
  trade.run(DAY, "ic-2", "open", ARM, 5960, 6040, null, 2.25, now, null, null, now, now);
  const leg = db.prepare(
    "INSERT INTO ic_spread_legs (ic_order_id, side, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
  );
  for (const id of ["ic-1", "ic-2"]) for (const side of ["put", "call"]) leg.run(id, side, id === "ic-1" ? "expired" : "open", now, now);
  const attempt = db.prepare(
    `INSERT INTO entry_attempts (ts, trade_date, ${spelling}, symbol, outcome, put_strike) VALUES (?, ?, ?, 'SPX', ?, 5950)`,
  );
  attempt.run(`${DAY}T10:00:00`, DAY, ARM, "filled");
  attempt.run(`${DAY}T10:05:00`, DAY, ARM, "cadence_blocked");
  // A second arm deciding at the same tick: divergence is only defined between two arms.
  attempt.run(`${DAY}T10:05:00`, DAY, `${ARM}-b`, "filled");
  // An advised twin with an experiment stamp, which the guide reads through its own query.
  db.prepare(
    `INSERT INTO ic_trades (trade_date, symbol, ic_order_id, status, ${spelling}, experiment_id, created_at, updated_at)
     VALUES (?, 'SPX', 'ic-3', 'open', ?, 'exp-1', ?, ?)`,
  ).run(DAY, `advised:${ARM}`, now, now);
  db.close();
  const config = tmpConfig(tmp);
  // The guide reads nothing without a risk-profile config; an empty registry lists the ledger's arms
  // as removed-from-config, which is the path that reads the arm column.
  const riskConfig = path.join(tmp, "config.risk.json");
  fs.writeFileSync(riskConfig, JSON.stringify({ profiles: {} }));
  (config.paths as { meicRiskConfig: string }).meicRiskConfig = riskConfig;
  return config;
}

const READERS: Record<string, (c: ConsoleConfig) => unknown> = {
  scope: (c) => readMeicScope(c, "paper", "ALL"),
  performance: (c) => readMeicPerformance(c, "paper", "session", null, null, "ALL"),
  performanceFilteredToTheArm: (c) => readMeicPerformance(c, "paper", "session", null, ARM, "ALL"),
  analytics: (c) => readMeicAnalytics(c, "paper", { ...NO_SCOPE, era: "ALL" }),
  analyticsScopedToTheArm: (c) => readMeicAnalytics(c, "paper", { ...NO_SCOPE, era: "ALL", profile: ARM }),
  forest: (c) => readMeicForest(c, "paper", DAY, { ...NO_SCOPE, era: "ALL" }),
  divergence: (c) => readMeicDivergence(c, "paper", DAY),
  occupancy: (c) => readOccupancy(c, "meic", "paper", DAY),
  attempts: (c) => readEntryAttempts(c, "meic", "paper", DAY),
  exitReasons: (c) => readExitReasons(c, "meic"),
  profileGuide: (c) => readMeicProfileGuide(c, "paper"),
};

// Only the two spellings a meic ledger has ever carried; `book`/`profile` belong to other modules.
const SPELLINGS = ["risk_profile", "arm"];

afterEach(() => closePooledDbs());

describe("every console reader of a meic ledger reads its arm column under either name", () => {
  for (const [name, read] of Object.entries(READERS)) {
    it(name, () => {
      const out = SPELLINGS.map((s) => {
        const payload = read(ledger(s));
        closePooledDbs();
        return JSON.stringify(payload);
      });
      for (const [i, s] of SPELLINGS.entries()) {
        expect(out[i], `${name} under \`${s}\` never names the arm -- read as empty`).toContain(ARM);
      }
      expect(out[1], `${name} reads differently under \`arm\` than under \`risk_profile\``).toBe(out[0]);
    });
  }
});
