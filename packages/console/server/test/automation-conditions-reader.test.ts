import { afterEach, describe, expect, it } from "vitest";
import { readAutomationConditionsFor, setAutomationConditionsCaller } from "../src/readers/automationConditions.js";

/** Today's conditions: the automations package's `cli conditions` document, put in the regime cuts' shape. */

const DOC = {
  ok: true,
  cut_version: 2,
  module: "automations",
  generated_at: "2026-11-23T09:00:00",
  session: "2026-11-23",
  symbol: "SPX",
  arm_column: "arm",
  thin_below_sessions: 3,
  min_effective_n: 14,
  era: { start: "2026-11-03", bounding_break: null, breaks: [], ignored_future: [], caveats: [] },
  arms: [
    {
      arm: "mine",
      sessions: 20,
      trades: 20,
      net_pnl: 50,
      dimensions: {
        trend: { sessions: 20, buckets: [{ bucket: "downtrend", sessions: 10, trades: 10, net_pnl: -50, avg_pnl: -5, thin: false, fragile: false }] },
      },
      today: {
        trend: { bucket: "downtrend", readable: true, why: null, sessions: 10, trades: 10, net_pnl: -50, avg_pnl: -5, thin: false, fragile: false },
        vol: { bucket: "high", readable: false, why: "degenerate" },
      },
    },
  ],
  cross_tabs: [],
  today: {
    session: "2026-11-23",
    at: "2026-11-23",
    buckets: { trend: "downtrend", vol: "high" },
    labels: { SPX: { trend: "downtrend", vol: "high", atr_percentile: 0.99, through: "2026-11-20" }, QQQ: { status: "unmeasured", reason: "needs 60 bars, has 39" } },
  },
};

let calls: string[] = [];
afterEach(() => {
  setAutomationConditionsCaller();
  calls = [];
});

describe("readAutomationConditionsFor", () => {
  it("puts the document in the regime cuts' shape and passes today's labels through", async () => {
    setAutomationConditionsCaller((name) => {
      calls.push(name);
      return { ok: true, error: null, json: DOC };
    });
    const out = await readAutomationConditionsFor("mine");
    expect(calls).toEqual(["mine"]);
    expect(out.error).toBeNull();
    expect(out.cuts?.module).toBe("automations");
    expect(out.cuts?.today?.buckets).toEqual({ trend: "downtrend", vol: "high" });
    const today = out.cuts?.arms[0]?.today ?? {};
    expect(today["trend"]?.readable).toBe(true);
    expect(today["trend"]?.netPnl).toBe(-50);
    expect(today["vol"]?.why).toBe("degenerate");
    expect(out.labels["SPX"]?.through).toBe("2026-11-20");
    expect(out.labels["QQQ"]?.reason).toBe("needs 60 bars, has 39");
  });

  it("names the module's refusal and a failed spawn, with no cut", async () => {
    setAutomationConditionsCaller(() => ({ ok: true, error: null, json: { ok: false, error: "no automation named 'x'" } }));
    expect(await readAutomationConditionsFor("x")).toEqual({ name: "x", error: "no automation named 'x'", cuts: null, labels: {} });
    setAutomationConditionsCaller(() => ({ ok: false, error: "the package is not installed", json: null }));
    expect((await readAutomationConditionsFor("y")).error).toBe("the package is not installed");
  });
});
