import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { ARM_COLUMNS, closePooledDbs } from "../src/readers/db.js";
import { readEarnings } from "../src/readers/earnings.js";
import { readExitReasons } from "../src/readers/exitReasons.js";

/**
 * The console's readers of an earnings ledger, run against the same rows under each name the arm
 * column has had: `profile` until earnings' own rename window, `arm` after it. Same guard, same
 * reason, as meic-arm-column.test.ts -- an empty payload is the failure, because that is what
 * `withReadOnlyDb` makes of "no such column".
 *
 * The schema is earnings' own (`fixtures/earnings-ledger-schema.sql`, generated from its `_conn()`
 * and pinned by earnings' test_console_schema_fixture.py).
 */

const ARM = "ctl-arm";
const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "earnings-ledger-schema.sql"), "utf-8");
const TABLES = ["trades", "scan_log", "entry_reviews", "management_events"];

function config(tmp: string): ConsoleConfig {
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

/** earnings' schema with its arm column spelled `spelling`; one closed trade, one open, one held-back event. */
function ledger(spelling: string): ConsoleConfig {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), `console-earnings-${spelling}-`));
  fs.mkdirSync(path.join(tmp, "earnings"));
  const db = new Database(path.join(tmp, "earnings", "paper_trades.db"));
  db.exec(SCHEMA);
  for (const table of TABLES) {
    const have = (db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>).map((c) => c.name);
    const current = ARM_COLUMNS.find((c) => have.includes(c));
    if (current === undefined) throw new Error(`fixture ${table} has no arm column`);
    if (current !== spelling) db.exec(`ALTER TABLE ${table} RENAME COLUMN ${current} TO ${spelling}`);
  }
  const opened = Date.parse("2026-09-23T19:00:00Z") / 1000;
  const trade = db.prepare(
    `INSERT INTO trades (order_id, strategy, symbol, expiration, ${spelling}, status, entry_credit, pnl,
       entry_cost, exit_cost, quantity, opened_at, closed_at, exit_reason)
     VALUES (?, 'iron_fly', ?, '2026-09-25', ?, ?, 1.5, ?, 2, 2, 1, ?, ?, ?)`,
  );
  trade.run("ord-1", "AAPL", ARM, "closed", 40, opened, opened + 64800, "profit_take");
  trade.run("ord-2", "MSFT", ARM, "open", null, opened, null, null);
  db.prepare(
    `INSERT INTO management_events (order_id, occurred_at, session_date, action, reason, executed, ${spelling})
     VALUES ('ord-2', ?, '2026-09-24', 'close', 'held_by_gate', 0, ?)`,
  ).run(opened + 3600, ARM);
  db.prepare(
    `INSERT INTO entry_reviews (scan_date, symbol, selected, ${spelling}) VALUES ('2026-09-23', 'AAPL', 1, ?)`,
  ).run(ARM);
  db.close();
  return config(tmp);
}

const READERS: Record<string, (c: ConsoleConfig) => unknown> = {
  trades: (c) => readEarnings(c, undefined, "ALL").trades,
  exitReasons: (c) => readExitReasons(c, "earnings"),
};

const SPELLINGS = ["profile", "arm"];

afterEach(() => closePooledDbs());

describe("every console reader of an earnings ledger reads its arm column under either name", () => {
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
      expect(out[1], `${name} reads differently under \`arm\` than under \`profile\``).toBe(out[0]);
    });
  }
});
