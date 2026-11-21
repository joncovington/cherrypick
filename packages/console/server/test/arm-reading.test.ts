import { afterEach, describe, expect, it } from "vitest";
import { readArmReading, setArmReadingCaller } from "../src/readers/armReading.js";
import { setBridgeCaller, type BridgeResult } from "../src/services/configBridge.js";
import { invalidateFeatures } from "../src/services/featuresBridge.js";

/**
 * A module arm against its backtest: the backtest package's reading passed through, its figures withheld
 * while licensed results are hidden, though which run matches is still named. Shown to fail: with the
 * licence gate removed, "withholds a licensed run's figures" served the reading.
 */

const MATCH = { run: "20261112-202157-b1f6a6", run_arm: "n1", window: "design", first: "2022-05-16", last: "2025-06-30", sessions: 778 };
const READING = { state: "inside", reference: { trades: 778 }, paper: { trades: 5 }, sample: { needed: 40, basis: "computed", have: 5 } };

function features(results: boolean) {
  invalidateFeatures();
  setBridgeCaller(() => ({ ok: true, capabilities: {}, modules: {}, services: {}, features: { backtest_results: results } }) as unknown as BridgeResult);
}

function cli(json: Record<string, unknown>) {
  setArmReadingCaller(() => ({ ok: true, error: null, json }));
}

afterEach(() => {
  setArmReadingCaller();
  setBridgeCaller();
  invalidateFeatures();
});

describe("a module arm's backtest reading", () => {
  it("passes the matching run and its reading through while licensed results are shown", async () => {
    features(true);
    cli({ ok: true, licensed: true, matches: [MATCH], chosen: MATCH, paper_from: "2026-11-03", reading: READING });
    const out = await readArmReading("meic", "pm1400-n1", 1);
    expect(out.withheld).toBe(false);
    expect(out.chosen).toEqual(MATCH);
    expect(out.paper_from).toBe("2026-11-03");
    expect(out.reading).toEqual(READING);
  });

  it("withholds a licensed run's figures, naming the run", async () => {
    features(false);
    cli({ ok: true, licensed: true, matches: [MATCH], chosen: MATCH, paper_from: "2026-11-03", reading: READING });
    const out = await readArmReading("meic", "pm1400-n1", 2);
    expect(out.withheld).toBe(true);
    expect(out.reading).toBeNull();
    expect(out.chosen?.run).toBe(MATCH.run);
  });

  it("says no run matches, with nothing to withhold", async () => {
    features(false);
    cli({ ok: true, licensed: true, matches: [], chosen: null, reading: null });
    const out = await readArmReading("meic", "bp-10k", 3);
    expect(out.withheld).toBe(false);
    expect(out.matches).toEqual([]);
  });

  it("says why when the package refuses", async () => {
    cli({ ok: false, error: "meic declares no arm 'nope'" });
    expect((await readArmReading("meic", "nope", 4)).error).toBe("meic declares no arm 'nope'");
  });
});
