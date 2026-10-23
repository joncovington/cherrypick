import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import type { Verdict } from "@console/shared";
import type { ConsoleConfig } from "../src/config.js";
import { closePooledDbs } from "../src/readers/db.js";
import { readEarnings, readEarningsAnalytics } from "../src/readers/earnings.js";
import { readVerdict, resetVerdictCache, setVerdictCaller } from "../src/services/verdictBridge.js";

/**
 * The strategies page's numbers come from the module's verdict; the session tiles and history are
 * this package's own readers. Both must leave the advised twins out of per-strategy figures (a
 * twin repeats its control's trade, so pooling counted every paired trade twice), and the history
 * must mark the rows the verdict's rule list flags rather than re-deciding which they are.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "earnings-ledger-schema.sql"), "utf-8");
let config: ConsoleConfig;

const VERDICT = {
  since: null,
  include_flagged: false,
  ci_level: 0.9,
  bootstrap: { seed: 1, draws: 10, cluster: "symbol + open session" },
  flag_rules: [],
  excluded: [],
  flagged: [{ order_id: "ctl-crm", arm: "strat_test:iron_fly", rule: "stranded_expiry", net: -100 }],
  breaks: [],
  arms: [],
  twins: [],
} satisfies Verdict;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-earnings-verdict-"));
  fs.mkdirSync(path.join(tmp, "earnings"));
  const db = new Database(path.join(tmp, "earnings", "paper_trades.db"));
  db.exec(SCHEMA);
  const ins = db.prepare(
    `INSERT INTO trades (order_id, strategy, symbol, expiration, entry_credit, exit_debit, pnl, opened_at,
       closed_at, quantity, entry_cost, exit_cost, status, exit_reason, capital_at_risk, arm)
     VALUES (?, 'iron_fly', ?, '2026-10-02', 5.0, 6.0, ?, 1790000000, 1790090000, 1, 0, 0, 'closed', 'stop_loss', 500, ?)`,
  );
  ins.run("ctl-acn", "ACN", -100, "strat_test:iron_fly");
  ins.run("twin-acn", "ACN", -120, "advised:take-earlier:iron_fly");
  ins.run("ctl-crm", "CRM", -100, "strat_test:iron_fly");
  db.close();
  config = {
    paths: { earningsDir: path.join(tmp, "earnings"), orchestratorConfig: path.join(tmp, "none.json") },
  } as unknown as ConsoleConfig;
});

afterEach(() => {
  setVerdictCaller();
  resetVerdictCache();
  closePooledDbs();
});

describe("earnings' per-strategy figures", () => {
  it("count the control arm only, never its advised twin", () => {
    const a = readEarningsAnalytics(config, "paper", "ALL");
    expect(a.kpis.closedTrades).toBe(2);
    expect(a.strategies).toEqual([expect.objectContaining({ strategy: "iron_fly", trades: 2, net: -200 })]);
  });
});

describe("earnings' history", () => {
  it("marks the rows the verdict flags, and only those", () => {
    setVerdictCaller(() => ({ ok: true, verdict: VERDICT, error: null }));
    const rows = readEarnings(config, undefined, "ALL").trades.rows;
    expect(Object.fromEntries(rows.map((r) => [r.orderId, r.flag]))).toEqual({
      "ctl-acn": null,
      "twin-acn": null,
      "ctl-crm": "stranded_expiry",
    });
  });

  it("still loads when the verdict is unavailable, every row unflagged", () => {
    setVerdictCaller(() => ({ ok: false, verdict: null, error: "not installed" }));
    expect(readEarnings(config, undefined, "ALL").trades.rows.every((r) => r.flag === null)).toBe(true);
  });
});

describe("the verdict bridge", () => {
  it("memoises per scope, so a polling page spawns once", () => {
    let calls = 0;
    setVerdictCaller(() => {
      calls += 1;
      return { ok: true, verdict: VERDICT, error: null };
    });
    readVerdict("2026-08-21", false, 0);
    readVerdict("2026-08-21", false, 1000);
    readVerdict("2026-08-21", true, 1000);
    expect(calls).toBe(2);
    readVerdict("2026-08-21", false, 200_000);
    expect(calls).toBe(3);
  });
});
