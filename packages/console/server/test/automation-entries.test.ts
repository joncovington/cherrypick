import { afterEach, describe, expect, it } from "vitest";
import { readAutomationEntriesFor, readAutomationsEntries, setAutomationEntriesCaller } from "../src/readers/automationEntries.js";
import { setAutomationsBuilderCaller } from "../src/services/automationsBuilderBridge.js";
import { resetBacktestCache, setBacktestCaller } from "../src/services/backtestBridge.js";
import { setBridgeCaller, type BridgeResult } from "../src/services/configBridge.js";
import { invalidateFeatures } from "../src/services/featuresBridge.js";

/**
 * The entries read: the automations package's funnel passed through, and for one automation its pinned
 * run's reasons handed to the same command, withheld as the reading withholds them. Shown to fail: with
 * the reading's licence gate removed, "sends no reasons for a withheld run" sent the licensed run's.
 */

const RUN = "builder-mine-2611201000";
const REASONS = { entered: 70, "gate:vix_level": 30 };
const FUNNEL = { due: 2, pending: 0, entered: 1, missed: 1, rate: 0.5, steps: [] };

function builder(reference: string | null) {
  setAutomationsBuilderCaller(() => ({
    ok: true,
    json: { ok: true, date: "2026-11-20", automations: [{ name: "mine", symbol: "SPX", blocks: [], reference, forward: null }] },
    error: null,
  }));
}

function features(results: boolean) {
  invalidateFeatures();
  setBridgeCaller(() => ({ ok: true, capabilities: {}, modules: {}, services: {}, features: { backtest_results: results } }) as unknown as BridgeResult);
}

function backtest(licensed: boolean) {
  setBacktestCaller(async () => ({
    ok: true,
    error: null,
    json: {
      ok: true,
      licensed,
      run: RUN,
      state: "inside",
      reference: { run: RUN, first: "2026-01-02", last: "2026-06-30", sessions: 100, trades: 70, reasons: REASONS },
      paper: { trades: 6 },
      sample: { needed: 40, basis: "computed", have: 6 },
    },
  }));
}

let argvs: string[][] = [];
function cli(json: Record<string, unknown>) {
  setAutomationEntriesCaller((argv) => {
    argvs.push(argv);
    return { ok: true, json, error: null };
  });
}

afterEach(() => {
  setAutomationsBuilderCaller();
  setAutomationEntriesCaller();
  setBacktestCaller();
  setBridgeCaller();
  invalidateFeatures();
  resetBacktestCache();
  argvs = [];
});

describe("the entries read", () => {
  it("lists every automation's funnel as the package gives it", async () => {
    cli({ ok: true, automations: [{ name: "mine", funnel: FUNNEL }] });
    const out = await readAutomationsEntries(1);
    expect(out.error).toBeNull();
    expect(out.automations[0]!.funnel).toEqual(FUNNEL);
    expect(argvs).toEqual([[]]);
  });

  it("says why when the package refuses", async () => {
    setAutomationEntriesCaller(() => ({ ok: false, json: null, error: "not installed" }));
    expect(await readAutomationsEntries(2)).toEqual({ automations: [], error: "not installed" });
  });

  it("hands the pinned run's reasons to the same command and passes both funnels through", async () => {
    builder(RUN);
    features(true);
    backtest(false);
    cli({ ok: true, name: "mine", first: "2026-11-16", last: "2026-11-17", paper: FUNNEL, run: { ...FUNNEL, rate: 0.7 }, causes: [], days: [] });
    const out = await readAutomationEntriesFor("mine", 3);
    expect(argvs).toEqual([["--name", "mine", "--run-reasons", JSON.stringify(REASONS)]]);
    expect(out.reference).toEqual({ run: RUN, licensed: false, first: "2026-01-02", last: "2026-06-30", sessions: 100 });
    expect(out.run?.rate).toBe(0.7);
    expect(out.paper).toEqual(FUNNEL);
  });

  it("sends no reasons for a withheld run, and paper's funnel stands alone", async () => {
    builder(RUN);
    features(false);
    backtest(true);
    cli({ ok: true, name: "mine", paper: FUNNEL, run: null, causes: [], days: [] });
    const out = await readAutomationEntriesFor("mine", 4);
    expect(argvs).toEqual([["--name", "mine"]]);
    expect(out.withheld).toBe(true);
    expect(out.reference).toBeNull();
    expect(out.run).toBeNull();
    expect(out.paper).toEqual(FUNNEL);
  });

  it("reads paper alone with nothing pinned", async () => {
    builder(null);
    cli({ ok: true, name: "mine", paper: FUNNEL, run: null, causes: [], days: [] });
    const out = await readAutomationEntriesFor("mine", 5);
    expect(argvs).toEqual([["--name", "mine"]]);
    expect(out.pinned).toBeNull();
    expect(out.paper).toEqual(FUNNEL);
  });
});
