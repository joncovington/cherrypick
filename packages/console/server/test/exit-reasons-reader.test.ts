import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readExitReasons } from "../src/readers/exitReasons.js";
import { closePooledDbs } from "../src/readers/db.js";

/**
 * Realized exit reasons + held-back verdicts, read directly off each module's own ledger. What
 * matters here: the (tag, exit_reason) grouping and its net/avg, the held-back JOIN actually
 * attributes to the right tag (not just the right event), and flies -- which carries neither
 * column nor table -- reports {unavailable} rather than a misleadingly empty table.
 */

function tmpConfig(): { config: ConsoleConfig; tmp: string } {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-exitreasons-"));
  const config = {
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
  return { config, tmp };
}

describe("readExitReasons", () => {
  it("groups closed positions by (tag, exit_reason) with net and avg net", () => {
    const { config, tmp } = tmpConfig();
    fs.mkdirSync(path.join(tmp, "curve"), { recursive: true });
    const db = new Database(path.join(tmp, "curve", "paper_trades.db"));
    db.exec(
      "CREATE TABLE curve_positions (position_id TEXT, book TEXT, status TEXT, exit_reason TEXT, gross_pnl REAL, fees REAL)",
    );
    const ins = db.prepare(
      "INSERT INTO curve_positions (position_id, book, status, exit_reason, gross_pnl, fees) VALUES (?,?,?,?,?,?)",
    );
    ins.run("p1", "control", "closed", "profit_take", 40.0, 2.0);
    ins.run("p2", "control", "closed", "profit_take", 60.0, 2.0);
    ins.run("p3", "control", "closed", "close_dte", 10.0, 2.0);
    ins.run("p4", "control", "open", null, null, null); // not closed -- excluded
    db.close();

    const out = readExitReasons(config, "curve");
    closePooledDbs();
    expect(Array.isArray(out.exitReasons)).toBe(true);
    const rows = out.exitReasons as Array<{ tag: string; reason: string; n: number; net: number; avgNet: number }>;
    const profitTake = rows.find((r) => r.reason === "profit_take");
    expect(profitTake).toMatchObject({ tag: "control", n: 2, net: 96.0, avgNet: 48.0 });
    const closeDte = rows.find((r) => r.reason === "close_dte");
    expect(closeDte).toMatchObject({ tag: "control", n: 1, net: 8.0 });
  });

  it("attributes held-back verdicts to the position's own tag via the join, not just the event", () => {
    const { config, tmp } = tmpConfig();
    fs.mkdirSync(path.join(tmp, "curve"), { recursive: true });
    const db = new Database(path.join(tmp, "curve", "paper_trades.db"));
    db.exec(
      "CREATE TABLE curve_positions (position_id TEXT, book TEXT, status TEXT, exit_reason TEXT, gross_pnl REAL, fees REAL)",
    );
    db.exec(
      "CREATE TABLE curve_management_events (position_id TEXT, action TEXT, reason TEXT, executed INTEGER, gate TEXT)",
    );
    db.prepare("INSERT INTO curve_positions VALUES ('p1','control','open',NULL,NULL,NULL)").run();
    db.prepare("INSERT INTO curve_positions VALUES ('p2','noflip','open',NULL,NULL,NULL)").run();
    // Same action/reason/gate on both tags -- the only thing distinguishing the two rows this
    // reader must report is which BOOK the position belongs to, via the join.
    db.prepare(
      "INSERT INTO curve_management_events VALUES ('p1','close','profit_take',0,'spread_too_wide')",
    ).run();
    db.prepare(
      "INSERT INTO curve_management_events VALUES ('p2','close','profit_take',0,'spread_too_wide')",
    ).run();
    db.prepare("INSERT INTO curve_management_events VALUES ('p1','close','profit_take',1,NULL)").run(); // executed -- excluded
    db.close();

    const out = readExitReasons(config, "curve");
    closePooledDbs();
    // If the grouping ever forgot `tag`, these two rows would collapse into one row with n=2 and
    // an arbitrary tag -- a book's held-back count silently blending into another book's.
    expect(out.heldBack).toHaveLength(2);
    const controlHeld = out.heldBack.find((r) => r.tag === "control");
    const noflipHeld = out.heldBack.find((r) => r.tag === "noflip");
    expect(controlHeld).toMatchObject({ action: "close", reason: "profit_take", gate: "spread_too_wide", n: 1 });
    expect(noflipHeld).toMatchObject({ action: "close", reason: "profit_take", gate: "spread_too_wide", n: 1 });
  });

  it("reports flies as unavailable rather than a misleadingly empty table", () => {
    const { config } = tmpConfig();
    const out = readExitReasons(config, "flies");
    expect(out.exitReasons).toEqual({ unavailable: expect.stringContaining("flies") as unknown as string });
    expect(out.heldBack).toEqual([]);
  });

  it("degrades to unavailable, not a crash, when the ledger doesn't exist yet", () => {
    const { config } = tmpConfig();
    const out = readExitReasons(config, "bwb");
    closePooledDbs();
    expect(out.exitReasons).toHaveProperty("unavailable");
    expect(out.heldBack).toEqual([]);
  });

  it("reports MEIC's exit reasons with an always-empty heldBack (no events table, not a missing one)", () => {
    const { config, tmp } = tmpConfig();
    fs.mkdirSync(path.join(tmp, "meic"), { recursive: true });
    const db = new Database(path.join(tmp, "meic", "paper_trades.db"));
    db.exec(
      "CREATE TABLE ic_trades (risk_profile TEXT, exit_time TEXT, exit_reason TEXT, pnl REAL, fees REAL)",
    );
    db.prepare(
      "INSERT INTO ic_trades VALUES ('control','2026-08-20T15:45:00','both_stopped',50.0,5.0)",
    ).run();
    db.close();

    const out = readExitReasons(config, "meic");
    closePooledDbs();
    const rows = out.exitReasons as Array<{ tag: string; reason: string; n: number }>;
    expect(rows).toEqual([{ tag: "control", reason: "both_stopped", n: 1, net: 45.0, avgNet: 45.0 }]);
    expect(out.heldBack).toEqual([]);
  });
});
