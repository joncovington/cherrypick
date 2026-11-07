import { describe, it, expect } from "vitest";
import { spawnSync } from "node:child_process";
import { suitePython } from "@console/shared/paths";
import { spawnModuleCliAsync, timedOut } from "../src/services/moduleCli.js";

/**
 * A module read killed at its time limit says it timed out. Before 2026-11-07 it said the package
 * "must be installed", which sent the reader to reinstall something that was there and merely slow
 * (a backtest run's report under load).
 */

const MESSAGE = "probe read unavailable — probe must be installed";
const hasPython = spawnSync(suitePython(), ["-c", "print(1)"], { encoding: "utf-8" }).status === 0;

describe("timedOut", () => {
  it("keeps the read's subject and drops the install hint", () => {
    expect(timedOut(MESSAGE, 120_000)).toBe("probe read unavailable — timed out after 120 s");
    expect(timedOut("bare message", 500)).toBe("bare message — timed out after 0.5 s");
  });
});

describe.skipIf(!hasPython)("spawnModuleCliAsync's limit", () => {
  it("says a read cut off at its limit timed out, not that the package is missing", async () => {
    const res = await spawnModuleCliAsync(["-c", "import time; time.sleep(20)"], MESSAGE, [0], 1_000);
    expect(res).toEqual({ ok: false, json: null, error: "probe read unavailable — timed out after 1 s" });
  });

  it("lets a read inside its limit answer", async () => {
    const res = await spawnModuleCliAsync(["-c", "import json; print(json.dumps({'ok': True}))"], MESSAGE, [0], 20_000);
    expect(res).toEqual({ ok: true, json: { ok: true }, error: null });
  });
});
