import { afterEach, describe, expect, it } from "vitest";
import { readAutomationGroups, setAutomationsGroupsCaller, shapeGroups } from "../src/services/automationsGroupsBridge.js";

const CLI = {
  ok: true,
  groups: {
    ensemble: {
      enabled: false,
      arms: [
        { name: "iron-condor-1345", enabled: false, verdict: "passed" },
        { name: "iron-fly-1000", enabled: false, verdict: "failed" },
        { name: "put-spread-1300", enabled: false, verdict: "no_verdict" },
        { name: "odd", enabled: true, verdict: "maybe" },
      ],
    },
    alpha: { enabled: true, arms: [] },
  },
};

afterEach(() => setAutomationsGroupsCaller());

describe("automation groups", () => {
  it("shapes the package's groups, sorted, with only known verdicts", () => {
    const groups = shapeGroups(CLI);
    expect(groups.map((g) => g.name)).toEqual(["alpha", "ensemble"]);
    const ens = groups[1]!;
    expect(ens.enabled).toBe(false);
    expect(ens.automations.map((a) => a.verdict)).toEqual(["passed", "failed", "no_verdict", null]);
    expect(ens.automations[3]!.enabled).toBe(true);
    expect(shapeGroups(null)).toEqual([]);
  });

  it("reads through the package and reports why it could not", async () => {
    setAutomationsGroupsCaller(() => ({ ok: true, json: CLI, error: null }));
    expect((await readAutomationGroups(1)).groups).toHaveLength(2);
    setAutomationsGroupsCaller(() => ({ ok: false, json: null, error: "not installed" }));
    expect(await readAutomationGroups(2)).toEqual({ groups: [], error: "not installed" });
  });
});
