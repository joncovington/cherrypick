/**
 * readers/contango.ts against a ledger built by the module's own schema (`cherrypick.contango.db
 * .connect`), so a column the reader names and the module drops fails here. Skips, visibly, where
 * the package is not importable (CI's console job has no Python packages installed).
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { afterAll, describe, expect, it } from "vitest";

import { loadConfig } from "../src/config.js";
import { readContango } from "../src/readers/contango.js";
import { closePooledDbs } from "../src/readers/db.js";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "contango-console-test-"));
const ledger = path.join(dir, "paper_trades.db");
const built =
  spawnSync("python", ["-c", `from cherrypick.contango import db; db.connect(r"${ledger}")`], {
    encoding: "utf-8",
    timeout: 60_000,
  }).status === 0;

afterAll(() => closePooledDbs());

describe.skipIf(!built)("readContango over the module's own schema", () => {
  it("reads a switch, its money, the NAV row and the fills", () => {
    const db = new Database(ledger);
    db.exec(`
      INSERT INTO contango_accounts (arm, starting_capital, cash, opened_session) VALUES ('control', 10000, 10.5, '2026-10-06');
      INSERT INTO contango_positions (position_id, arm, symbol, role, shares, entry_session, entry_mid, entry_value,
        entry_fees, entry_slippage, entry_ratio, status, exit_session, exit_mid, exit_value, exit_fees, exit_slippage,
        exit_ratio, exit_reason, distributions, gross_pnl, fees, slippage, net_pnl)
      VALUES ('control:SVXY:2026-10-06', 'control', 'SVXY', 'risk', 199, '2026-10-06', 50.0, -9950.0, 0, 1.99, 0.85,
        'closed', '2026-10-07', 51.0, 10149.0, 0.06, 2.03, 0.98, 'switch_to_cash', 0, 199.0, 0.06, 4.02, 194.92);
      INSERT INTO contango_positions (position_id, arm, symbol, role, shares, entry_session, entry_mid, entry_value,
        entry_fees, entry_slippage, entry_ratio, status, distributions)
      VALUES ('control:SHV:2026-10-07', 'control', 'SHV', 'cash', 91, '2026-10-07', 110.4, -10046.4, 0, 2.01, 0.98, 'open', 0);
      INSERT INTO contango_sessions (trade_date, arm, ratio, state_before, state_after, action, holding_symbol, shares, mark, cash, nav)
      VALUES ('2026-10-07', 'control', 0.98, 'risk', 'cash', 'switch', 'SHV', 91, 110.4, 10.5, 10057.9);
      INSERT INTO contango_marks (trade_date, symbol, bid, ask, mid) VALUES ('2026-10-07', 'SHV', 110.39, 110.41, 110.40);
      INSERT INTO contango_loop_iterations (ran_at, session_date, phase, status) VALUES (1791300000, '2026-10-07', 'decision', 'ok');
    `);
    db.close();

    const config = loadConfig();
    config.paths.contangoDir = dir;
    const out = readContango(config);

    expect(out.dbPresent).toBe(true);
    expect(out.session).toBe("2026-10-07");
    const closed = out.stints.find((s) => s.status === "closed")!;
    expect(closed.entryValue! + closed.exitValue! + closed.distributions).toBeCloseTo(closed.grossPnl!, 2);
    expect(closed.grossPnl! - closed.fees! - closed.slippage!).toBeCloseTo(closed.netPnl!, 2);
    const open = out.stints.find((s) => s.status === "open")!;
    expect(open.mark).toBe(110.4);
    expect(open.unrealisedNet).toBeCloseTo(91 * 110.4 - 10046.4 - 2.01, 2);
    const control = out.arms.find((a) => a.arm === "control")!;
    expect(control.holding?.symbol).toBe("SHV");
    expect(control.switches).toBe(1);
    expect(control.latestNav).toBe(10057.9);
    expect(out.fills.map((f) => f.side).sort()).toEqual(["buy", "buy", "sell"]);
    expect(out.fills.find((f) => f.side === "sell")!.slippageBps).toBeCloseTo((2.03 / 10149) * 1e4, 1);
  });

  it("a missing store is absent, never fabricated", () => {
    const config = loadConfig();
    config.paths.contangoDir = path.join(dir, "nowhere");
    const out = readContango(config);
    expect(out.dbPresent).toBe(false);
    expect(out.stints).toEqual([]);
  });
});
