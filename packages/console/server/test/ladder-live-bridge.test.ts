import { afterEach, describe, expect, it } from "vitest";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { normaliseChecks, readLadderLiveStatus, resetLadderLiveStatusCache, setLadderLiveCaller, shapeStatus } from "../src/services/ladderLiveBridge.js";

/**
 * The bridge is the one place `live_loop --status` becomes the console's. The document below has the
 * module's shape (live_loop.status, live_gates.Gate.as_dict, live_tick._write_checks); a rename on either
 * side fails here, and the last test asks the module itself.
 */

afterEach(() => {
  setLadderLiveCaller();
  resetLadderLiveStatusCache();
});

const GATES = [
  { name: "live.enabled", ok: true, reason: "on", blocks: "all" },
  { name: "live.gate0_confirmed", ok: true, reason: "a human attestation is on file", blocks: "all" },
  { name: "modules.ladder.enabled", ok: true, reason: "the module is switched on in the suite config", blocks: "all" },
  { name: "arm.record", ok: false, reason: "arm stamp is None, today is 2026-11-20 -- arming is per-day", blocks: "all" },
  { name: "account.designated", ok: true, reason: "a designated account is set (****1234)", blocks: "all" },
  { name: "halt.flag", ok: false, reason: "the halt flag is set: no new buy; held lots keep their resting sells", blocks: "new_buys" },
  { name: "live.arm", ok: true, reason: "'session' is an enabled paper arm", blocks: "all" },
  { name: "live.symbols", ok: true, reason: "TQQQ", blocks: "all" },
  { name: "live.limits", ok: true, reason: "lot, caps, breaker and hours are valid", blocks: "all" },
];

const STATUS = {
  ok: true,
  module: "ladder",
  order_path: true,
  dry_run: true,
  mode: "dry-run",
  note: "the order path ships in dry-run",
  held: { unresolved: ["ldr.b.TQQQ.20261120.1"], unrecorded: { "ldr.s.TQQQ.20261120.1": "9001" } },
  checks: {
    at: "2026-11-20T09:01:00-05:00",
    date: "2026-11-20",
    positions: {
      readable: true,
      blocked: { SOXL: "positions_shortfall: broker holds 0, ledger 10" },
      funds: {
        TQQQ: { ledger: 20, broker: 20, status: "match", suspected_split: null },
        SOXL: { ledger: 10, broker: 0, status: "shortfall", suspected_split: null },
      },
    },
    splits: { declared: [{ fund: "TQQQ", ex_date: "2026-12-01", to: 2, per: 1 }], problems: ["splits[1] has no fund"] },
    intents: [{ what: "place buy TQQQ ldr.b.TQQQ.20261120.2", reason: "level", lot: null, ok: true, shares: 20, limit: 48.5, tif: "GTC_EXT" }],
  },
  today: "2026-11-20",
  armed_today: false,
  armed_for: null,
  account: "5WZ04242",
  halt_flag: true,
  ready: false,
  new_buys_allowed: false,
  unmet_gates: ["arm.record", "halt.flag"],
  gates: GATES,
};

describe("shapeStatus", () => {
  it("carries the nine gates with their reasons, the arm, the halt flag and the held submissions", () => {
    const s = shapeStatus({ ok: true, json: STATUS, error: null });
    expect(s).toMatchObject({ ok: true, error: null, mode: "dry-run", ready: false, newBuysAllowed: false, armedToday: false, armedFor: null, haltFlag: true });
    expect(s.gates).toHaveLength(9);
    expect(s.gates[3]).toEqual({ name: "arm.record", ok: false, reason: "arm stamp is None, today is 2026-11-20 -- arming is per-day", blocks: "all" });
    expect(s.gates[5]!.blocks).toBe("new_buys");
    expect(s.unmetGates).toEqual(["arm.record", "halt.flag"]);
    expect(s.held).toEqual({ unresolved: ["ldr.b.TQQQ.20261120.1"], unrecorded: 1 });
  });

  it("masks the account whatever the module sent", () => {
    const s = shapeStatus({ ok: true, json: STATUS, error: null });
    expect(s.account).toBe("****4242");
    expect(JSON.stringify(s)).not.toContain("5WZ04242");
  });

  it("carries the positions cross-check, the declared splits and the standing intents", () => {
    const c = shapeStatus({ ok: true, json: STATUS, error: null }).checks!;
    expect(c.funds).toEqual([
      { fund: "TQQQ", ledger: 20, broker: 20, status: "match", suspectedSplit: null },
      { fund: "SOXL", ledger: 10, broker: 0, status: "shortfall", suspectedSplit: null },
    ]);
    expect(c.blocked).toEqual([{ fund: "SOXL", reason: "positions_shortfall: broker holds 0, ledger 10" }]);
    expect(c.splitsDeclared).toEqual([{ fund: "TQQQ", exDate: "2026-12-01", ratio: "2:1" }]);
    expect(c.splitProblems).toEqual(["splits[1] has no fund"]);
    expect(c.intents).toEqual([{ what: "place buy TQQQ ldr.b.TQQQ.20261120.2", reason: "level", lot: null, shares: 20, limit: 48.5, tif: "GTC_EXT" }]);
  });

  it("reads no checks before the loop has ticked (the module writes an empty object)", () => {
    expect(normaliseChecks({})).toBeNull();
    expect(shapeStatus({ ok: true, json: { ...STATUS, checks: {} }, error: null }).checks).toBeNull();
  });

  it("is live only on an explicit false, as the module reads live.dry_run", () => {
    const live = (dry: unknown) => shapeStatus({ ok: true, json: { ...STATUS, dry_run: dry }, error: null }).mode;
    expect(live(false)).toBe("live");
    for (const v of [true, "false", null, 0, undefined]) expect(live(v), String(v)).toBe("dry-run");
  });

  it("takes the stricter reading when the module says ready beside a failing gate", () => {
    const lying = { ...STATUS, ready: true, new_buys_allowed: true, gates: GATES.map((g) => (g.name === "live.limits" ? { ...g, ok: false, reason: "lot_dollars must be a positive number" } : g)) };
    const s = shapeStatus({ ok: true, json: lying, error: null });
    expect(s.ready).toBe(false);
    expect(s.newBuysAllowed).toBe(false);
  });

  it("is a failure, never a verdict, when the module cannot be read or answers without gates", () => {
    for (const res of [
      { ok: false, json: null, error: "ladder live status unavailable" },
      { ok: true, json: { ok: false, error: "no config" }, error: null },
      { ok: true, json: { ok: true, ready: true, gates: [] }, error: null },
      { ok: true, json: { ok: true, gates: GATES }, error: null }, // no ready
    ]) {
      const s = shapeStatus(res);
      expect(s.ok).toBe(false);
      expect(s.error).toBeTruthy();
      expect(s.ready).toBeNull(); // neither ready nor not ready
      expect(s.mode).toBeNull();
      expect(s.gates).toEqual([]);
    }
  });
});

describe("readLadderLiveStatus", () => {
  it("asks the module once for a burst of polls", async () => {
    let calls = 0;
    setLadderLiveCaller(() => {
      calls += 1;
      return { ok: true, json: STATUS, error: null };
    });
    const t = 1_000_000;
    await Promise.all([readLadderLiveStatus(t), readLadderLiveStatus(t + 1_000), readLadderLiveStatus(t + 2_000)]);
    expect(calls).toBe(1);
  });
});

// --------------------------------------------------------------------------- against the module itself
const REPO = path.resolve(__dirname, "..", "..", "..", "..");

/** `live_loop --status` from THIS checkout's source, in a throwaway home so no machine state is read. */
function moduleStatus(): Record<string, unknown> | null {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ladder-live-status-"));
  const script = [
    "import os, sys",
    "root = sys.argv[1]",
    "sys.path.insert(0, os.path.join(root, 'packages', 'core'))",
    "sys.path.insert(0, os.path.join(root, 'packages', 'ladder', 'src'))",
    "from cherrypick.ladder import live_loop",
    "raise SystemExit(live_loop.main(['--status']))",
  ].join("\n");
  const out = spawnSync("python", ["-I", "-c", script, REPO], {
    encoding: "utf-8",
    timeout: 60_000,
    env: { ...process.env, CHERRYPICK_HOME: home },
  });
  if (out.status !== 0 || typeof out.stdout !== "string") return null;
  try {
    return JSON.parse(out.stdout) as Record<string, unknown>;
  } catch {
    return null;
  }
}

const theirs = moduleStatus();

describe.skipIf(theirs === null)("the bridge reads what the module itself prints", () => {
  it("shapes the nine gates and a verdict, and never arms anything", () => {
    const s = shapeStatus({ ok: true, json: theirs, error: null });
    expect(s.ok, s.error ?? "").toBe(true);
    expect(s.gates.map((g) => g.name)).toEqual([
      "live.enabled",
      "live.gate0_confirmed",
      "modules.ladder.enabled",
      "arm.record",
      "account.designated",
      "halt.flag",
      "live.arm",
      "live.symbols",
      "live.limits",
    ]);
    // In an empty home the example config is not live: a clean machine is not ready and is dry-run.
    expect(s.ready).toBe(false);
    expect(s.mode).toBe("dry-run");
    expect(s.armedToday).toBe(false);
    expect(s.unmetGates).toContain("live.enabled");
    expect(s.account === null || /^\*{4}.{0,4}$/.test(s.account)).toBe(true);
  });
});
