import { afterEach, describe, expect, it } from "vitest";
import { lineFrom, readGroupUnits, setGroupsReadCaller } from "../src/readers/automationGroupsRead.js";

/**
 * The group read: the automations package's answer passed through, its lines put in the performance
 * page's own shape and nothing else computed. Shown to fail: with `net_stress` read as `netStress`, the
 * line test read 0 for the stress net.
 */

const LINE = {
  trades: 2,
  net: -200,
  gross: -194,
  fees: 2,
  settlement: 0,
  slippage: 4,
  net_stress: -202,
  net_mid: -196,
  wins: 1,
  mean: -100,
  worst: -500,
  drawdown: 500,
  curve: [
    { x: "2026-11-02", y: 300 },
    { x: "2026-11-03", y: -200 },
  ],
};

afterEach(() => setGroupsReadCaller());

describe("the group read", () => {
  it("puts the package's line in the page's shape", () => {
    const l = lineFrom(LINE);
    expect([l.netStress, l.netMid, l.drawdown, l.curve.length]).toEqual([-202, -196, 500, 2]);
    expect(lineFrom(undefined)).toMatchObject({ trades: 0, net: 0, mean: null, curve: [] });
  });

  it("passes every group and the automations in none through", async () => {
    setGroupsReadCaller(() => ({
      ok: true,
      error: null,
      json: {
        ok: true,
        groups: [{ name: "g", members: ["a", "b"], line: LINE, risk: { peak: 1600, session: "2026-11-06", cap: 2000, of_cap: 0.8 }, together: null }],
        ungrouped: { members: ["c"], line: { ...LINE, net: 999 } },
      },
    }));
    const out = await readGroupUnits(1);
    expect(out.error).toBeNull();
    expect(out.groups[0]!.risk).toEqual({ peak: 1600, session: "2026-11-06", cap: 2000, of_cap: 0.8 });
    expect(out.groups[0]!.line.netStress).toBe(-202);
    expect(out.ungrouped).toMatchObject({ members: ["c"], line: { net: 999 } });
  });

  it("says why when the package refuses", async () => {
    setGroupsReadCaller(() => ({ ok: true, error: null, json: { ok: false, error: "groups.x: no arm belongs to this group" } }));
    expect((await readGroupUnits(2)).error).toBe("groups.x: no arm belongs to this group");
  });
});
