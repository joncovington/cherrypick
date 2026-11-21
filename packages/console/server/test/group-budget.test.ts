import { afterEach, describe, expect, it } from "vitest";
import { readGroupBudgetFor } from "../src/readers/groupBudget.js";
import { setAutomationsBuilderCaller } from "../src/services/automationsBuilderBridge.js";
import { resetBacktestCache, setBacktestCaller, type BacktestRequest } from "../src/services/backtestBridge.js";
import { setBridgeCaller, type BridgeResult } from "../src/services/configBridge.js";
import { invalidateFeatures } from "../src/services/featuresBridge.js";

/**
 * A group's budget from its members' pinned runs: the members (not retired) from the module's groups view,
 * each pin from the Builder read, the reading from the backtest package, passed through. Nothing is read
 * until every member has a pin; a licensed reading is withheld while licensed results are not shown.
 */

const member = (name: string, retired: string | null = null) => ({ name, retired, state: retired ? "retired" : "off_group" });

function bridge(results: boolean) {
  invalidateFeatures();
  setBridgeCaller((req) => {
    if (req.op === "automation_groups") {
      return { ok: true, groups: [{ name: "ensemble", enabled: false, members: [member("iron-condor-1345"), member("long-put-1030"), member("iron-fly-1000", "2026-11-21")] }], ungrouped: [] } as unknown as BridgeResult;
    }
    return { ok: true, capabilities: {}, modules: {}, services: {}, features: { backtest_results: results } } as unknown as BridgeResult;
  });
}

function builder(refs: Record<string, string | null>) {
  setAutomationsBuilderCaller(() => ({
    ok: true,
    json: { ok: true, date: "2026-11-21", automations: Object.entries(refs).map(([name, reference]) => ({ name, symbol: "SPX", blocks: [], reference, forward: null })) },
    error: null,
  }));
}

let spawned: BacktestRequest[] = [];
function backtest(licensed: boolean) {
  setBacktestCaller(async (req) => {
    spawned.push(req);
    return { ok: true, error: null, json: { ok: true, licensed, max_risk: 1300, shares: { "iron-condor-1345": 77, "long-put-1030": 23.1 }, members: [] } };
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

const IC = "builder-iron-condor-1345-2611201000";
const LP = "builder-long-put-1030-2611201000";

describe("a group's budget from its backtests", () => {
  it("names the members without a pinned run and asks the backtest package nothing", async () => {
    bridge(true);
    builder({ "iron-condor-1345": IC, "long-put-1030": null, "iron-fly-1000": null });
    backtest(false);
    const out = await readGroupBudgetFor("ensemble");
    expect(out.missing).toEqual(["long-put-1030"]); // the retired one is not asked for
    expect(out.reading).toBeNull();
    expect(spawned).toEqual([]);
  });

  it("sends each member and its own pinned run, and passes the reading through", async () => {
    bridge(true);
    builder({ "iron-condor-1345": IC, "long-put-1030": LP, "iron-fly-1000": null });
    backtest(false);
    const out = await readGroupBudgetFor("ensemble");
    expect(spawned).toEqual([{ kind: "groupBudget", pairs: [`iron-condor-1345=${IC}`, `long-put-1030=${LP}`] }]);
    expect(out.reading?.max_risk).toBe(1300);
    expect(out.withheld).toBe(false);
  });

  it("withholds a licensed reading while licensed results are not shown", async () => {
    bridge(false);
    builder({ "iron-condor-1345": IC, "long-put-1030": LP });
    backtest(true);
    const out = await readGroupBudgetFor("ensemble");
    expect(out.withheld).toBe(true);
    expect(out.reading).toBeNull();
  });

  it("refuses a pin that is not the automation's own run", async () => {
    bridge(true);
    builder({ "iron-condor-1345": LP, "long-put-1030": LP });
    backtest(false);
    expect((await readGroupBudgetFor("ensemble")).missing).toEqual(["iron-condor-1345"]);
  });
});
