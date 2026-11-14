import { describe, expect, it } from "vitest";
import type { ReviewArm, ReviewModule } from "@console/shared";
import { attention, moduleAnchor, shownArms, splitModules } from "../src/pages/Review/summary";

function mod(over: Partial<ReviewModule>): ReviewModule {
  return {
    module: "meic",
    ok: true,
    reason: null,
    loopTicked: true,
    iterations: 10,
    errors: 0,
    closed: 0,
    net: 0,
    gross: 0,
    cost: 0,
    wins: 0,
    capitalAtRisk: null,
    onMaxRisk: null,
    n: 0,
    events: null,
    singleEvent: false,
    breaks: [],
    suspectedBreak: null,
    expectedBasis: null,
    expected: null,
    observed: null,
    carriedPositions: 0,
    carriedCapital: null,
    carriedCapitalUnknown: 0,
    arms: [],
    ...over,
  };
}

const arm = (name: string, net: number): ReviewArm => ({ arm: name, closed: 1, net, wins: net > 0 ? 1 : 0, capitalAtRisk: null, onMaxRisk: null, centredBy: null });

describe("which modules get a row", () => {
  it("a trade, a carried position or an unreadable store makes a row; the rest share the idle line", () => {
    const { rows, idle } = splitModules([
      mod({ module: "meic", closed: 12 }),
      mod({ module: "calendars", carriedPositions: 4 }),
      mod({ module: "earnings", ok: false, reason: "locked" }),
      mod({ module: "contango" }),
      mod({ module: "flies", loopTicked: false }),
    ]);
    expect(rows.map((m) => m.module)).toEqual(["meic", "calendars", "earnings"]);
    expect(idle).toEqual([
      { module: "contango", note: null },
      { module: "flies", note: "loop did not tick" },
    ]);
  });
});

describe("an open row's arms", () => {
  it("shows the top three by net, then counts the rest", () => {
    const arms = [arm("a", -50), arm("b", 200), arm("c", 10), arm("d", 90), arm("e", -5)];
    expect(shownArms(arms, false)).toEqual({ shown: [arm("b", 200), arm("d", 90), arm("c", 10)], more: 2 });
    expect(shownArms(arms, true).shown.map((a) => a.arm)).toEqual(["b", "d", "c", "e", "a"]);
    expect(shownArms(arms.slice(0, 3), false).more).toBe(0);
  });
});

describe("what needs attention", () => {
  it("names the module each flag is about, so it can link to the row", () => {
    const flags = attention([mod({ module: "flies", loopTicked: false }), mod({ module: "bwb", suspectedBreak: { ratio: 0.1, trades: 3, trailingMedian: 25 } })], "final");
    expect(flags.map((f) => [f.module, f.level])).toEqual([
      ["flies", "err"],
      ["bwb", "warn"],
    ]);
  });

  it("a provisional session names each module carrying positions, or says so plainly when none is", () => {
    const carried = attention([mod({ module: "calendars", carriedPositions: 4, carriedCapital: 191 })], "provisional");
    expect(carried).toHaveLength(1);
    expect(carried[0]!.module).toBe("calendars");
    expect(carried[0]!.text).toContain("carried 4 position(s) overnight, 191.00 at risk");
    const none = attention([mod({})], "provisional");
    expect(none).toHaveLength(1);
    expect(none[0]!.module).toBeNull();
    expect(attention([mod({ carriedPositions: 4 })], "final")).toEqual([]);
  });

  it("an unreadable store is an error, and nothing else is said about it", () => {
    expect(attention([mod({ ok: false, reason: "locked", loopTicked: false })], "final")).toEqual([{ level: "err", module: "meic", text: "could not be read — locked" }]);
  });
});

it("anchors are safe ids", () => {
  expect(moduleAnchor("meic.alt login")).toBe("review-mod-meic-alt-login");
});
