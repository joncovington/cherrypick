import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { etWallClock, forModel, readFliesAgent, readFliesAgentPack } from "../src/readers/fliesAgent.js";
import { closePooledDbs } from "../src/readers/db.js";

/**
 * The flies intraday agent's page reader. Pinned: the qualification file is shaped, never judged
 * (a `pass: null` stays null, and the offered modes are the writer's); check times are New York wall
 * clock, which the chart family reads off characters 11-16; a decision is "followed" only under
 * `read_decision`'s rule; open tags and gate refusals come from the ledger for the arms in view only;
 * and a pack loses its `_` keys as `for_model` removes them. The contract case runs the real writer
 * (`intraday_eval.run`) and checks the reader carries its numbers unchanged.
 */

const REPO = path.resolve(__dirname, "..", "..", "..", "..");
const FLIES_PKG = path.join(REPO, "packages", "flies");
const DAY = "2026-10-06";
/** 2026-10-06 10:00 New York (EDT, UTC-4). */
const TEN_AM = Date.UTC(2026, 9, 6, 14, 0, 0) / 1000;

let tmp: string;
let config: ConsoleConfig;

function cfg(root: string): ConsoleConfig {
  fs.mkdirSync(path.join(root, "data", "flies", "intraday_agent"), { recursive: true });
  fs.mkdirSync(path.join(root, "state"), { recursive: true });
  fs.mkdirSync(path.join(root, "config"), { recursive: true });
  return {
    port: 0,
    paths: {
      cherrypick: root,
      fliesDir: path.join(root, "data", "flies"),
      fliesConfig: path.join(root, "config", "flies.json"),
    },
  } as unknown as ConsoleConfig;
}

function ledger(): Database.Database {
  const db = new Database(path.join(config.paths.fliesDir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE fly_positions (position_id TEXT, trade_date TEXT, arm TEXT, side TEXT, center REAL, wing_width REAL,
      quantity INTEGER, credit REAL, status TEXT, close_tag_at TEXT, close_tag_source TEXT, close_tag_spot REAL,
      close_tag_natural REAL, close_tag_mid REAL, close_tag_fees REAL);
    CREATE TABLE fly_entry_attempts (ts TEXT, trade_date TEXT, arm TEXT, outcome TEXT, block_detail TEXT, center REAL, spot REAL);
  `);
  return db;
}

function writeRecords(lines: unknown[]): void {
  fs.writeFileSync(
    path.join(config.paths.fliesDir, "intraday_agent", `${DAY}.jsonl`),
    lines.map((l) => JSON.stringify(l)).join("\n") + "\n{torn",
  );
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "flies-agent-"));
  config = cfg(tmp);
});

afterEach(() => {
  closePooledDbs();
  fs.rmSync(tmp, { recursive: true, force: true });
});

describe("the flies agent reader", () => {
  it("writes check times as New York wall clock, not UTC", () => {
    expect(etWallClock(TEN_AM)).toBe("2026-10-06T10:00:00");
  });

  it("shapes the session's checks, skips a torn line, and reads the open off the first pack", () => {
    const pack = { spx: { open: 7700.5, last: 7712.25 }, flies: { _position_ids: { p1: "real" } } };
    writeRecords([
      { target: "paper", session: DAY, as_of: TEN_AM, called: false, skipped: "no_trigger" },
      {
        target: "paper", session: DAY, as_of: TEN_AM + 300, called: true, trigger: "near_band", model: "opus",
        cost_usd: 0.17, seconds: 21.4, ok: true,
        decision: { trend_gate: "off", close_labels: ["p1"], dropped_closes: [{ label: "p2", why: "past_wing_width" }], confidence: 0.7, reason: "chop" },
        pack,
      },
      { target: "live", session: DAY, as_of: TEN_AM + 360, called: true, model: "opus", cost_usd: 0.2, ok: true, decision: { trend_gate: "on" } },
    ]);
    const out = readFliesAgent(config, DAY, new Date((TEN_AM + 600) * 1000));
    expect(out.checks.map((c) => [c.at.slice(11, 16), c.called, c.target])).toEqual([
      ["10:00", false, "paper"],
      ["10:05", true, "paper"],
      ["10:06", true, "live"],
    ]);
    const call = out.checks[1]!;
    expect([call.trendGate, call.closeLabels, call.droppedCloses, call.spot, call.index]).toEqual(["off", ["p1"], ["p2"], 7712.25, 1]);
    expect(out.dayOpen).toBe(7700.5);
    // Paper only: the live shadow's call is not this session's paper spend.
    expect(out.sessionSpend).toEqual({ checks: 2, calls: 1, costUsd: 0.17 });
    expect(out.qualificationStatus).toBe("absent");
  });

  it("follows a decision only for the session in view, unexpired and admissible", () => {
    const write = (o: Record<string, unknown>) =>
      fs.writeFileSync(path.join(tmp, "state", "flies-intraday-advice-paper.json"), JSON.stringify(o));
    const base = { session: DAY, as_of: TEN_AM, expires_at: TEN_AM + 600, ok: true, decision: { trend_gate: "off" }, model: "opus" };
    const at = (s: number) => readFliesAgent(config, DAY, new Date(s * 1000)).decision;
    write(base);
    expect(at(TEN_AM + 120)).toMatchObject({ fresh: true, trendGate: "off", ageSeconds: 120, at: "2026-10-06T10:00:00" });
    expect(at(TEN_AM + 601)?.fresh).toBe(false);
    write({ ...base, ok: false });
    expect(at(TEN_AM + 120)?.fresh).toBe(false);
    write({ ...base, session: "2026-10-05" });
    expect(at(TEN_AM + 120)?.fresh).toBe(false);
  });

  it("reads open tags and gate refusals for the rule and agent arms only", () => {
    const db = ledger();
    const ins = db.prepare(
      "INSERT INTO fly_positions (position_id, trade_date, arm, side, center, wing_width, quantity, credit, status, close_tag_at, close_tag_natural) VALUES (?, ?, ?, 'call', 7720, 5, 1, 2.4, ?, ?, 3.0)",
    );
    ins.run("open-agent", DAY, "intraday-agent", "open", `${DAY}T11:00:00`);
    ins.run("settled-agent", DAY, "intraday-agent", "settled", `${DAY}T11:00:00`);
    ins.run("open-control", DAY, "control", "open", `${DAY}T11:00:00`);
    ins.run("untagged", DAY, "trend-rule", "open", null);
    const att = db.prepare("INSERT INTO fly_entry_attempts VALUES (?, ?, ?, ?, ?, 7720, 7712)");
    att.run(`${DAY}T10:30:00`, DAY, "trend-rule", "gate_blocked", "completion_against_drift");
    att.run(`${DAY}T10:31:00`, DAY, "control", "gate_blocked", "completion_against_drift");
    att.run(`${DAY}T10:32:00`, DAY, "intraday-agent", "gate_blocked", "credit_below_floor");
    db.close();
    const out = readFliesAgent(config, DAY);
    expect(out.openTags.map((t) => t.positionId)).toEqual(["open-agent"]);
    expect(out.refusals.map((r) => [r.arm, r.at])).toEqual([["trend-rule", `${DAY}T10:30:00`]]);
  });

  it("keeps a pass the writer could not score as null, and never re-derives the offered modes", () => {
    fs.writeFileSync(
      path.join(config.paths.fliesDir, "intraday_agent_qualification.json"),
      JSON.stringify({
        schema: 1,
        arms: { control: "control", rule: "trend-rule", agent: "intraday-agent" },
        criteria: [
          { id: "decision_sessions", mode: "gates", label: "x", value: 25, threshold: 20, pass: true },
          { id: "live_shadow", mode: "gates", label: "y", value: 6, threshold: 5, pass: null },
        ],
        // Deliberately inconsistent with the criteria: the page shows the writer's list, not its own.
        offered_modes: ["off", "shadow", "gates"],
        per_session: [{ session: DAY, decided: true, rule: { settled_net: -100 }, agent: { settled_net: 40 }, control: null }],
      }),
    );
    const q = readFliesAgent(config, null).qualification!;
    expect(q.criteria.map((c) => c.pass)).toEqual([true, null]);
    expect(q.offeredModes).toEqual(["off", "shadow", "gates"]);
    expect(q.perSession[0]).toMatchObject({ agentLessRule: 140, control: null });
  });

  it("serves the pack as the model saw it", () => {
    expect(forModel({ a: 1, _ids: { p1: "x" }, b: [{ _x: 1, y: 2 }] })).toEqual({ a: 1, b: [{ y: 2 }] });
    writeRecords([{ target: "paper", as_of: TEN_AM, called: true, pack: { flies: { _position_ids: { p1: "real" }, entries: 1 } } }]);
    expect(readFliesAgentPack(config, DAY, 0)?.pack).toEqual({ flies: { entries: 1 } });
    expect(readFliesAgentPack(config, DAY, 5)).toBeNull();
  });
});

// --------------------------------------------------------------------------- the writer's contract
const WRITER = String.raw`
import json, sys
from pathlib import Path
from cherrypick.flies import close_tags, db, intraday_eval
root = Path(sys.argv[1])
conn = db.connect(str(root / "paper.db"))
def row(pid, arm, kind, pnl, **tag):
    conn.execute(
        "INSERT INTO fly_positions (position_id, book_id, trade_date, arm, symbol, kind, side, center, wing_width, "
        "quantity, credit, status, gross_pnl, fees, pnl) VALUES (?, ?, '2026-10-06', ?, 'SPX', ?, 'call', 7720, 5, 1, "
        "2.4, 'settled', ?, 0, ?)",
        (pid, "2026-10-06:" + arm + ":SPX", arm, kind, pnl, pnl),
    )
    for k, v in tag.items():
        conn.execute("UPDATE fly_positions SET " + k + " = ? WHERE position_id = ?", (v, pid))
row("c", "control", "fly", 240.0)
row("r", "trend-rule", "short_vertical", -260.0)
row("a", "intraday-agent", "short_vertical", -260.0, close_tag_natural=3.0, close_tag_mid=2.6,
    close_tag_fees=close_tags.round_trip_fees("SPX", 1), close_tag_source="agent", close_tag_at="2026-10-06T11:00:00")
conn.commit()
target = root / "q.json"
intraday_eval.qualification_path = lambda: target
intraday_eval.live_db_path = lambda: root / "no-live.db"
intraday_eval.load_records = lambda: {"2026-10-06": [{"target": "paper", "called": True, "ok": True, "model": "opus", "cost_usd": 0.17}]}
intraday_eval.run(conn, {"intraday_agent": {"enabled": True}}, write=True)
print(target.read_text(encoding="utf-8"))
`;

function runWriter(dir: string): Record<string, unknown> | null {
  if (!fs.existsSync(path.join(FLIES_PKG, "run.py"))) return null;
  const out = spawnSync("python", ["-c", WRITER, dir], { cwd: FLIES_PKG, encoding: "utf-8", timeout: 120_000 });
  if (out.status !== 0 || typeof out.stdout !== "string") return null;
  try {
    return JSON.parse(out.stdout) as Record<string, unknown>;
  } catch {
    return null;
  }
}

const writerDir = fs.mkdtempSync(path.join(os.tmpdir(), "flies-agent-writer-"));
const written = runWriter(writerDir);

describe.skipIf(written === null)("the reader carries intraday_eval's file unchanged", () => {
  it("every figure on the page is the writer's", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "flies-agent-contract-"));
    const c = cfg(root);
    fs.writeFileSync(path.join(c.paths.fliesDir, "intraday_agent_qualification.json"), JSON.stringify(written));
    const q = readFliesAgent(c, DAY).qualification!;
    const w = written!;
    const day = (w["per_session"] as Record<string, Record<string, Record<string, number>>>[])[0]!;
    expect(q.arms).toEqual(w["arms"]);
    expect(q.trendBandPoints).toBe(w["trend_band_points"]);
    expect(q.offeredModes).toEqual(w["offered_modes"]);
    expect(q.criteria.map((x) => [x.id, x.value, x.pass])).toEqual(
      (w["criteria"] as Record<string, unknown>[]).map((x) => [x["id"], x["value"], x["pass"]]),
    );
    const agent = q.perSession[0]!.agent!;
    expect([agent.settledNet, agent.netCloses, agent.netCloses2x, agent.stranded, agent.tagged]).toEqual([
      day["agent"]!["settled_net"], day["agent"]!["net_closes"], day["agent"]!["net_closes_2x"], day["agent"]!["stranded"], day["agent"]!["tagged"],
    ]);
    const close = (w["tagged_closes"] as Record<string, unknown>[])[0]!;
    expect(q.taggedCloses[0]).toMatchObject({
      positionId: close["position_id"], natural: close["natural"], fees: close["fees"],
      settledNet: close["settled_net"], closedNet: close["closed_net"], closedNet2x: close["closed_net_2x"], saved: close["saved"],
    });
    expect(q.spend[0]).toMatchObject({ session: DAY, target: "paper", calls: 1, costUsd: 0.17 });
    const shadow = w["shadow"] as Record<string, unknown>;
    expect([q.shadow.counted, q.shadow.agree, q.liveClosesBuilt]).toEqual([shadow["counted"], shadow["agree"], w["live_closes_built"]]);
    fs.rmSync(root, { recursive: true, force: true });
  });
});
