import { afterEach, describe, expect, it } from "vitest";
import { readAutomationReadingFor } from "../src/readers/automationReading.js";
import { setAutomationsBuilderCaller } from "../src/services/automationsBuilderBridge.js";
import { resetBacktestCache, setBacktestCaller, type BacktestRequest } from "../src/services/backtestBridge.js";
import { setBridgeCaller, type BridgeResult } from "../src/services/configBridge.js";
import { invalidateFeatures } from "../src/services/featuresBridge.js";

/**
 * One automation's reading: the pin and the declared sample come from the module's Builder read, the
 * reading from the backtest package, passed through; a licensed run's figures are withheld while licensed
 * results are not shown. Shown to fail: with the licence gate removed, "withholds a licensed run" served
 * the figures.
 */

const RUN = "builder-mine-2611201000";

function builder(reference: string | null, forward: Record<string, unknown> | null = null) {
  setAutomationsBuilderCaller(() => ({
    ok: true,
    json: { ok: true, date: "2026-11-20", automations: [{ name: "mine", symbol: "SPX", blocks: [], reference, forward }] },
    error: null,
  }));
}

function features(results: boolean) {
  invalidateFeatures();
  setBridgeCaller(() => ({ ok: true, capabilities: {}, modules: {}, services: {}, features: { backtest_results: results } }) as unknown as BridgeResult);
}

let spawned: BacktestRequest[] = [];
function backtest(licensed: boolean) {
  setBacktestCaller(async (req) => {
    spawned.push(req);
    return {
      ok: true,
      error: null,
      json: { ok: true, licensed, run: RUN, state: "inside", reference: { run: RUN, trades: 100 }, paper: { trades: 6 }, sample: { needed: 40, basis: "declared", have: 6 } },
    };
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

describe("an automation's reading", () => {
  it("says no run is pinned, and asks the backtest package nothing", async () => {
    builder(null);
    backtest(false);
    expect(await readAutomationReadingFor("mine")).toEqual({ name: "mine", pinned: null, withheld: false, error: null, reading: null });
    expect(spawned).toEqual([]);
  });

  it("passes the pinned run and the forward rule's min_obs, and the reading through", async () => {
    builder(RUN, { min_obs: 40 });
    backtest(false);
    features(false);
    const out = await readAutomationReadingFor("mine");
    expect(spawned).toEqual([{ kind: "reading", name: "mine", run: RUN, declared: 40 }]);
    expect(out.pinned).toBe(RUN);
    expect(out.reading?.state).toBe("inside");
    expect(out.reading?.licensed).toBe(false);
  });

  it("withholds a licensed run's figures while licensed results are not shown", async () => {
    builder(RUN);
    backtest(true);
    features(false);
    const out = await readAutomationReadingFor("mine");
    expect(out.withheld).toBe(true);
    expect(out.reading).toBeNull();
  });

  it("shows a licensed run while licensed results are shown", async () => {
    builder(RUN);
    backtest(true);
    features(true);
    const out = await readAutomationReadingFor("mine");
    expect(out.withheld).toBe(false);
    expect(out.reading?.licensed).toBe(true);
  });

  it("refuses a pin not named for this automation, and an unknown automation", async () => {
    builder("builder-other-2611201000");
    backtest(false);
    expect((await readAutomationReadingFor("mine")).error).toContain("not this automation's");
    expect((await readAutomationReadingFor("nope")).error).toBe("no automation named nope");
    expect(spawned).toEqual([]);
  });
});
