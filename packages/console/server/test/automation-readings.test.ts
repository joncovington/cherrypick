import { afterEach, describe, expect, it } from "vitest";
import { readAllAutomationReadings } from "../src/readers/automationReadings.js";
import { setAutomationsBuilderCaller } from "../src/services/automationsBuilderBridge.js";
import { resetBacktestCache, setBacktestCaller, type BacktestRequest } from "../src/services/backtestBridge.js";
import { setBridgeCaller, type BridgeResult } from "../src/services/configBridge.js";
import { invalidateFeatures } from "../src/services/featuresBridge.js";

/**
 * Every automation's reading for the vs-backtest tab: the pins and declared samples from the module's
 * Builder read, every pinned reading from one backtest read, passed through; a licensed run's figures
 * withheld while licensed results are not shown. Shown to fail: with the licence check removed from the
 * reader, "withholds a licensed run" served the figures.
 */

const A = "builder-a-2611201000";
const B = "builder-b-2611201000";

function builder(rows: Array<{ name: string; reference: string | null; forward?: Record<string, unknown> | null; group?: string | null }>) {
  setAutomationsBuilderCaller(() => ({
    ok: true,
    json: { ok: true, date: "2026-11-20", automations: rows.map((r) => ({ symbol: "SPX", blocks: [], forward: null, group: null, ...r })) },
    error: null,
  }));
}

function features(results: boolean) {
  invalidateFeatures();
  setBridgeCaller(() => ({ ok: true, capabilities: {}, modules: {}, services: {}, features: { backtest_results: results } }) as unknown as BridgeResult);
}

const ROW = (run: string, licensed: boolean) => ({
  ok: true,
  run,
  licensed,
  state: "inside",
  paper: { trades: 9, counts: { inside: 7, below: 1, above: 1 }, net: 128, mean: 14.2, entry_rate: 0.64 },
  sample: { needed: 40, basis: "declared", have: 9 },
  reference: { trades: 1206, mean: 35, entry_rate: 0.71, min_trades: null },
});

let spawned: BacktestRequest[] = [];
function backtest(readings: Record<string, unknown>) {
  setBacktestCaller(async (req) => {
    spawned.push(req);
    return { ok: true, error: null, json: { ok: true, readings } };
  });
}

afterEach(() => {
  setAutomationsBuilderCaller();
  setBacktestCaller();
  setBridgeCaller();
  invalidateFeatures();
  resetBacktestCache();
  spawned = [];
});

describe("every automation against its backtest", () => {
  it("reads every pin in one request, with each forward rule's min_obs, and lists the unpinned too", async () => {
    builder([
      { name: "a", reference: A, forward: { min_obs: 40 }, group: "g" },
      { name: "b", reference: B },
      { name: "c", reference: null },
    ]);
    features(false);
    backtest({ a: ROW(A, false), b: { ok: false, run: B, error: "no Builder run" } });
    const out = await readAllAutomationReadings();
    expect(spawned).toEqual([{ kind: "readings", pairs: [`a=${A}:40`, `b=${B}`] }]);
    expect(out.error).toBeNull();
    const [a, b, c] = out.automations;
    expect(a).toMatchObject({ name: "a", group: "g", pinned: A, state: "inside", withheld: false });
    expect(a?.paper?.counts).toEqual({ inside: 7, below: 1, above: 1 });
    expect(b).toMatchObject({ name: "b", pinned: B, error: "no Builder run", state: null });
    expect(c).toMatchObject({ name: "c", pinned: null, state: null, error: null });
  });

  it("asks the backtest package nothing when nothing is pinned, and refuses a pin that is not the automation's", async () => {
    builder([
      { name: "a", reference: null },
      { name: "b", reference: "builder-other-2611201000" },
    ]);
    backtest({});
    const out = await readAllAutomationReadings();
    expect(spawned).toEqual([]);
    expect(out.automations[1]?.error).toBe("the pinned run's name is not this automation's");
  });

  it("withholds a licensed run's figures while licensed results are not shown", async () => {
    builder([{ name: "a", reference: A }]);
    backtest({ a: ROW(A, true) });
    features(false);
    const hidden = (await readAllAutomationReadings()).automations[0];
    expect(hidden).toMatchObject({ withheld: true, state: null, paper: null, reference: null });
    resetBacktestCache();
    features(true);
    const shown = (await readAllAutomationReadings()).automations[0];
    expect(shown).toMatchObject({ withheld: false, state: "inside" });
  });
});
