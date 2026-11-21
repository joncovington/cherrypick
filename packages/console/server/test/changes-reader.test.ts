import { afterEach, describe, expect, it } from "vitest";
import { readModuleChanges, setChangesCallers } from "../src/readers/changes.js";
import { setBridgeCaller, type BridgeResult } from "../src/services/configBridge.js";
import { invalidateFeatures } from "../src/services/featuresBridge.js";

/**
 * The changes read: core's list passed through, and for automations the backtest's declared items
 * handed to core, a licensed one left out while licensed results are hidden. Shown to fail: with the
 * filter on `licensed` removed, "leaves a licensed declared item out" sent it to core.
 */

const ROW = { kind: "arm", name: "bp-10k", reading: "leaning", rows: [] };
const DOC = { ok: true, module: "meic", status: "ok", reason: null, min_sessions: 10, counts: { leaning: 1 }, rows: [ROW] };

function features(results: boolean) {
  invalidateFeatures();
  setBridgeCaller(() => ({ ok: true, capabilities: {}, modules: {}, services: {}, features: { backtest_results: results } }) as unknown as BridgeResult);
}

let coreArgv: string[][] = [];
let declaredCalls = 0;
function callers(items: Array<Record<string, unknown>>) {
  setChangesCallers({
    core: (argv) => {
      coreArgv.push(argv);
      return { ok: true, error: null, json: DOC };
    },
    declared: () => {
      declaredCalls += 1;
      return { ok: true, error: null, json: { ok: true, items } };
    },
  });
}

afterEach(() => {
  setChangesCallers();
  setBridgeCaller();
  invalidateFeatures();
  coreArgv = [];
  declaredCalls = 0;
});

describe("the changes read", () => {
  it("passes a module's list through and asks the backtest nothing outside automations", async () => {
    callers([]);
    const out = await readModuleChanges("meic", 1);
    expect(coreArgv).toEqual([["--module", "meic"]]);
    expect(declaredCalls).toBe(0);
    expect(out.rows).toEqual([ROW]);
    expect(out.counts).toEqual({ leaning: 1 });
  });

  it("hands the automations' declared items to core", async () => {
    features(true);
    const items = [{ item: "vix_high", licensed: true }, { item: "stop_2x", licensed: false }];
    callers(items);
    const out = await readModuleChanges("automations", 2);
    expect(coreArgv).toEqual([["--module", "automations", "--declares", JSON.stringify({ items })]]);
    expect(out.withheld).toBe(0);
  });

  it("leaves a licensed declared item out while licensed results are hidden, and counts it", async () => {
    features(false);
    callers([{ item: "vix_high", licensed: true }, { item: "unsure" }, { item: "stop_2x", licensed: false }]);
    const out = await readModuleChanges("automations", 3);
    expect(coreArgv).toEqual([["--module", "automations", "--declares", JSON.stringify({ items: [{ item: "stop_2x", licensed: false }] })]]);
    expect(out.withheld).toBe(2);
  });

  it("says why when core refuses", async () => {
    setChangesCallers({ core: () => ({ ok: true, error: null, json: { ok: false, error: "ValueError: boom" } }) });
    const out = await readModuleChanges("meic", 4);
    expect(out.error).toBe("ValueError: boom");
    expect(out.rows).toEqual([]);
  });
});
