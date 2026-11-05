import { afterEach, describe, expect, it } from "vitest";
import { LEG, readManageModel, setManageModelCaller } from "../src/services/manageModelBridge.js";

/**
 * Model a trade, bridged from packages/manage: a bad request is refused before any process starts,
 * manage's answer is passed through camelised, and manage's own refusal reads as one. Nothing here is
 * an order path: the command is `model`, which prices and sends nothing.
 */

afterEach(() => setManageModelCaller());

describe("readManageModel", () => {
  it("refuses what manage could not read, without starting a process", async () => {
    const calls: string[][] = [];
    setManageModelCaller((argv) => {
      calls.push(argv);
      return { ok: true, json: { ok: true }, error: null };
    });
    expect((await readManageModel("aaa; rm", ["-1 2026-12-18 46P"])).error).toBe("the underlying must be a ticker");
    expect((await readManageModel("AAA", [])).error).toBe("no leg to model");
    expect((await readManageModel("AAA", ["sell the 46 put"])).error).toContain("cannot read leg");
    expect((await readManageModel("AAA", Array(7).fill("-1 2026-12-18 46P"))).error).toBe("at most 6 legs");
    expect(calls).toEqual([]);
  });

  it("runs manage's model with each leg and passes its answer through", async () => {
    let seen: string[] = [];
    setManageModelCaller((argv) => {
      seen = argv;
      return {
        ok: true,
        error: null,
        json: {
          ok: true,
          session: "2026-11-04",
          underlying: "AAA",
          net: 105.0,
          worst_case: -4395.0,
          worst_case_at: 0.0,
          unbounded: false,
          hv_30_day: 0.24,
          legs: [{ leg: "-1 2026-12-18 45P", price: 1.05, price_source: "the price given", iv: 0.3, delta: -0.2, theta_day: 1.4 }],
          caps: [{ label: "this trade's worst case / net liq", value: 0.32, cap: 0.025, over: true }],
          after: { underlying: "AAA", pnl_now: 30.0, curves: [] },
        },
      };
    });
    const r = await readManageModel("aaa", ["-1 2026-12-18 45P @ 1.05", "+1 2026-12-18 40P"]);
    expect(seen).toEqual(["-m", "cherrypick.manage", "model", "--underlying", "AAA", "--leg", "-1 2026-12-18 45P @ 1.05", "--leg", "+1 2026-12-18 40P"]);
    expect(r).toMatchObject({ ok: true, error: null, net: 105, worstCase: -4395, worstCaseAt: 0, hv30Day: 0.24 });
    expect(r.legs[0]).toMatchObject({ priceSource: "the price given", thetaDay: 1.4 });
    expect(r.caps[0]!.over).toBe(true);
    expect(r.after).toMatchObject({ pnlNow: 30 });
  });

  it("reads manage's refusal and a dead process as refusals", async () => {
    setManageModelCaller(() => ({ ok: true, error: null, json: { ok: false, error: "no price for AAA in the snapshot" } }));
    expect(await readManageModel("AAA", ["-1 2026-12-18 46P"])).toMatchObject({ ok: false, error: "no price for AAA in the snapshot" });
    setManageModelCaller(() => ({ ok: false, error: "python is not there", json: null }));
    expect((await readManageModel("AAA", ["-1 2026-12-18 46P"])).error).toBe("python is not there");
  });

  it("reads the leg text manage reads", () => {
    for (const ok of ["-1 2026-12-18 46P", "+2 2026-12-18 46.5C @ 1.05", "-1 2027-01-15 5000P @ 40"]) expect(LEG.test(ok)).toBe(true);
    for (const bad of ["-1 2026-12-18 46", "-1 12/18 46P", "-1 2026-12-18 46P; rm -rf", "1 2026-12-18 46X"]) expect(LEG.test(bad)).toBe(false);
  });
});
