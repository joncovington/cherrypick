import { afterEach, describe, expect, it } from "vitest";
import { readAutomationsBuilder, setAutomationsBuilderCaller, shapeBuilder } from "../src/services/automationsBuilderBridge.js";

const ROW = {
  name: "mine",
  group: null,
  symbol: "SPX",
  enabled: true,
  note: "a new automation",
  start: "2026-11-18",
  register: null,
  summary: "structure, entry, gates, exit, size not backtested",
  blocks: [
    { block: "entry", state: "not_backtested", keys: { entry: { kind: "time", at: "10:30" } } },
    { block: "exit", state: "odd", keys: {} },
  ],
  forward: { measure: "session_sum", field: "net_pnl", min_obs: 30 },
  suggestion: {
    blocks: ["entry", "exit"],
    window: ["2025-11-13", "2026-11-13"],
    possible: false,
    needs: [
      {
        field: "chain",
        expiry: "0dte",
        cadence: "1m",
        covered: false,
        source: null,
        nearest: { source: "recorded-0dte", first: "2026-10-13", last: "2026-11-13", why: "starts 2026-10-13" },
      },
    ],
  },
};
const CLI = { ok: true, date: "2026-11-16", automations: [ROW, { name: "iron-condor-1345", symbol: "SPX", register: { item: 75, verdict: "passed" }, blocks: [], suggestion: null }] };

afterEach(() => setAutomationsBuilderCaller());

describe("the automation builder read", () => {
  it("shapes each automation, its blocks, labels and suggestion", () => {
    const [mine, condor] = shapeBuilder(CLI);
    expect(mine!.start).toBe("2026-11-18");
    expect(mine!.blocks.map((b) => b.state)).toEqual(["not_backtested", "not_backtested"]); // an unknown label is not a pass
    expect(mine!.blocks[0]!.keys).toEqual({ entry: { kind: "time", at: "10:30" } });
    expect(mine!.suggestion!.needs[0]!.nearest!.first).toBe("2026-10-13");
    expect(mine!.suggestion!.possible).toBe(false);
    // Fail closed: a suggestion or need that does not say it is our own reads as licensed.
    expect(mine!.suggestion!.licensed).toBe(true);
    expect(mine!.suggestion!.needs[0]!.licensed).toBe(true);
    const own = shapeBuilder({ automations: [{ ...ROW, suggestion: { ...ROW.suggestion, licensed: false, needs: [{ ...ROW.suggestion.needs[0], licensed: false }] } }] });
    expect(own[0]!.suggestion!.licensed).toBe(false);
    expect(own[0]!.suggestion!.needs[0]!.licensed).toBe(false);
    expect(condor!.register).toEqual({ item: 75, verdict: "passed" });
    expect(condor!.suggestion).toBeNull();
    expect(condor!.forward).toBeNull();
    expect(shapeBuilder(null)).toEqual([]);
  });

  it("reads through the package and reports why it could not", async () => {
    setAutomationsBuilderCaller(() => ({ ok: true, json: CLI, error: null }));
    const got = await readAutomationsBuilder(1);
    expect(got.date).toBe("2026-11-16");
    expect(got.automations).toHaveLength(2);
    setAutomationsBuilderCaller(() => ({ ok: false, json: null, error: "not installed" }));
    expect(await readAutomationsBuilder(2)).toEqual({ date: null, automations: [], error: "not installed" });
  });
});
