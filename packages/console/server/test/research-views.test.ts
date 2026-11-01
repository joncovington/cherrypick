import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import Fastify, { type FastifyInstance } from "fastify";
import { loadConfig, type ConsoleConfig } from "../src/config.js";
import { registerBacktestRoutes } from "../src/routes/backtest.js";
import { readDimension, readSlices, shapeSlice } from "../src/readers/slices.js";
import { setBridgeCaller, type BridgeResult } from "../src/services/configBridge.js";
import { invalidateFeatures } from "../src/services/featuresBridge.js";
import { shapeExecution } from "../src/services/executionBridge.js";

/** B10's execution record and B5's slice reports: shaped as the Python side wrote them, nothing decided here. */

const EXEC = {
  meic: {
    ok: true,
    mirror: "live-shadow",
    weeks: [
      {
        week: "2026-W42",
        net: 43.04,
        trades: 2,
        orders: 2,
        fill_rate: 1,
        slip_dollars: -1.49,
        slip_pct_width_mean: -0.37,
        minutes_to_fill_mean: 28.8,
        overrides: [{ kind: "config_changed_in_session", session: "2026-10-15", at: "x" }],
        clean: false,
      },
    ],
    gap: { since: "2026-10-15", sessions: 3, total: -133.17, parts: { missed: -172.48, fill: -5.75 }, largest: "missed" },
    window: { window: 50, n: 3, mean: 21.5, se: null, baseline: 12.0, z: null, would: "too_few" },
  },
  bwb: { ok: false, reason: "no live ledger" },
};

describe("the execution record", () => {
  it("renames the orchestrator's reply and keeps every module", () => {
    const out = shapeExecution({ ok: true, json: EXEC, error: null });
    expect(out.ok).toBe(true);
    const [meic, bwb] = out.data!.modules;
    expect(meic).toMatchObject({ module: "meic", ok: true, mirror: "live-shadow" });
    expect(meic!.weeks[0]).toMatchObject({ fillRate: 1, slipDollars: -1.49, clean: false });
    expect(meic!.gap).toMatchObject({ total: -133.17, largest: "missed" });
    expect(meic!.window).toMatchObject({ n: 3, would: "too_few", baseline: 12 });
    expect(bwb).toMatchObject({ module: "bwb", ok: false, reason: "no live ledger", weeks: [] });
  });

  it("says why when the subprocess fails", () => {
    expect(shapeExecution({ ok: false, json: null, error: "boom" })).toEqual({ ok: false, data: null, error: "boom" });
  });
});

const SLICE = {
  label: "bwb_132/bounce/all",
  in_sample: true,
  unit: "R",
  trades: 42,
  overall: { n: 42, mean: -0.1, ci95: [-0.4, 0.2], greyed: false },
  cells: 2,
  ranked_cells: 1,
  dimensions: { environment: { uptrend: { n: 35, mean: 0.05, ci95: [-0.1, 0.2], greyed: false }, sideways: { n: 7, mean: -0.8, ci95: null, greyed: true } } },
  note: "In-sample ...",
};

describe("the slice reports", () => {
  it("shape a report and refuse a file that is not one", () => {
    const r = shapeSlice("a.json", SLICE)!;
    expect(r).toMatchObject({ unit: "R", trades: 42, cells: 2, rankedCells: 1 });
    expect(r.dimensions[0]!.cells.map((c) => [c.value, c.greyed])).toEqual([
      ["uptrend", false],
      ["sideways", true],
    ]);
    expect(shapeSlice("b.json", { label: "x" })).toBeNull(); // not labelled in-sample: not a slice report
  });

  it("read each dimension in plain terms: one group, too few, separated or overlapping", () => {
    const cell = (value: string, mean: number, lo: number, hi: number, greyed = false) => ({
      value,
      n: 100,
      mean,
      ci95: [lo, hi] as [number, number],
      greyed,
      sessions: null,
      placebo: null,
    });
    expect(readDimension("holding", [cell("same day", 0.1, 0.05, 0.15)]).read).toBe("one-group");
    expect(readDimension("environment", [cell("uptrend", 0.1, 0, 0.2), cell("sideways", 0.3, 0.1, 0.5, true)]).read).toBe("too-few");
    const sep = readDimension("entry_hour", [cell("09:00", 0.05, 0.02, 0.08), cell("13:00", 0.18, 0.17, 0.19), cell("11:00", 0.08, 0.06, 0.1)]);
    expect([sep.read, sep.best?.value, sep.worst?.value]).toEqual(["separated", "13:00", "09:00"]);
    const ov = readDimension("environment", [cell("mixed", 0.11, 0.1, 0.12), cell("uptrend", 0.12, 0.105, 0.125)]);
    expect([ov.read, ov.best?.value, ov.worst?.value]).toEqual(["overlapping", "uptrend", "mixed"]);
  });

  it("reads the label and the overall interval against zero", () => {
    const r = shapeSlice("a.json", SLICE)!;
    expect(r.analysis).toMatchObject({ schema: "bwb_132", arm: "bounce", month: null, overall: "unclear", greyedCells: 1 });
    expect(r.analysis.dimensions[0]).toMatchObject({ name: "environment", read: "too-few", ranked: 1, greyed: 1 });
    const pos = shapeSlice("c.json", { ...SLICE, label: "meic_ic/control/2026-10", overall: { n: 9, mean: 0.1, ci95: [0.05, 0.15], greyed: false } })!;
    expect(pos.analysis).toMatchObject({ arm: "control", month: "2026-10", overall: "positive" });
  });

  it("carry a cell's days and its random-day placebo, and null where an older report has neither", () => {
    const withDays = {
      ...SLICE,
      dimensions: {
        weekday: {
          Tue: { n: 40, mean: 0.2, ci95: [0.1, 0.3], greyed: false, sessions: 12, placebo: { sessions: 12, of: 60, mean: 0.8, draws: 2000, beaten_by: 0.031 } },
          Mon: { n: 30, mean: 0.1, ci95: [0.0, 0.2], greyed: true, sessions: 4 },
        },
      },
    };
    const [tue, mon] = shapeSlice("d.json", withDays)!.dimensions[0]!.cells;
    expect(tue).toMatchObject({ sessions: 12, placebo: { sessions: 12, of: 60, beatenBy: 0.031 } });
    expect(mon).toMatchObject({ sessions: 4, placebo: null, greyed: true });
    const old = shapeSlice("a.json", SLICE)!.dimensions[0]!.cells[0]!;
    expect([old.sessions, old.placebo]).toEqual([null, null]);
  });

  it("reads every report in the directory and skips a broken file", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "slices-"));
    const config = { paths: { technicalsDir: tmp } } as unknown as ConsoleConfig;
    expect(readSlices(config).data!.reports).toEqual([]);
    fs.mkdirSync(path.join(tmp, "slices"));
    fs.writeFileSync(path.join(tmp, "slices", "a.json"), JSON.stringify(SLICE));
    fs.writeFileSync(path.join(tmp, "slices", "b.json"), "{not json");
    expect(readSlices(config).data!.reports.map((r) => r.file)).toEqual(["a.json"]);
  });
});

function features(on: boolean): BridgeResult {
  return { ok: true, capabilities: {}, modules: {}, services: {}, features: { backtest: on } } as unknown as BridgeResult;
}

describe("the slices route sits behind the backtest gate", () => {
  let app: FastifyInstance;
  beforeAll(async () => {
    app = Fastify();
    registerBacktestRoutes(app, loadConfig());
    await app.ready();
  });
  afterAll(async () => app.close());
  afterEach(() => {
    setBridgeCaller();
    invalidateFeatures();
  });

  it("404s while the gate is closed and answers once it is open", async () => {
    setBridgeCaller(() => features(false));
    expect((await app.inject({ method: "GET", url: "/api/backtest/slices" })).statusCode).toBe(404);
    invalidateFeatures();
    setBridgeCaller(() => features(true));
    const open = await app.inject({ method: "GET", url: "/api/backtest/slices" });
    expect(open.statusCode).toBe(200);
    expect(open.json()).toMatchObject({ ok: true });
  });
});
