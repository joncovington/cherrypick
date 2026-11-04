import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ArmRailEntry } from "../src/components/Attempts";
import type { ForestArm } from "../src/pages/Meic/MeicForestCard";
import { ArmTileView, armTiles } from "../src/pages/Meic/MeicArmCards";

/** MEIC's arms board (2026-11-04): a tile per arm from the forest and attempts reads the page makes. */

const prices = [5800, 5850, 5900];

function forestArm(arm: string, entered: number, realisedNet: number, stopped = 0, open = entered - stopped): ForestArm {
  return {
    arm,
    positions: [],
    prices,
    pnl: [-100, 200, -100],
    outcome: { entered, stopped, expired: 0, open, realisedNet },
    perPosition: [],
  };
}

function rail(arm: string, attempts: number, fills: number, refusals: Record<string, number>): ArmRailEntry {
  return {
    arm,
    attempts,
    fills,
    sessionsSeen: 1,
    sessionsWithFills: fills > 0 ? 1 : 0,
    resolvedToday: null,
    doubleStoppedToday: null,
    refusals,
    lastRefusal: null,
    lastAttemptTs: null,
    lastFillTs: null,
  };
}

function text(html: string): string {
  return html.replace(/<!-- -->/g, "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

describe("the MEIC arm tiles", () => {
  it("orders arms by realised net high to low, and names each arm's commonest refusal and its share", () => {
    const tiles = armTiles(
      [forestArm("a", 4, 120), forestArm("b", 5, -80, 2), forestArm("c", 3, 300)],
      [rail("a", 100, 4, { cadence_blocked: 60, window_blocked: 36 }), rail("b", 200, 5, { sign_rule_blocked: 50 }), rail("c", 40, 3, {})],
    );
    expect(tiles.map((t) => t.arm)).toEqual(["c", "a", "b"]);
    expect(tiles.find((t) => t.arm === "a")!.refusal).toEqual({ reason: "cadence", share: 0.6 });
    expect(tiles.find((t) => t.arm === "c")!.refusal).toBeNull();
  });

  it("sets control apart while it trades on another scale, and sorts it last whatever its net", () => {
    const tiles = armTiles([forestArm("control", 296, 900), forestArm("a", 5, 10), forestArm("b", 6, -40)], []);
    expect(tiles.map((t) => t.arm)).toEqual(["a", "b", "control"]);
    expect(tiles.find((t) => t.arm === "control")!.otherScale).toBe(true);
    // A control on the arms' own scale is an arm like the rest.
    const same = armTiles([forestArm("control", 6, 900), forestArm("a", 5, 10), forestArm("b", 6, -40)], []);
    expect(same[0]!.arm).toBe("control");
    expect(same[0]!.otherScale).toBe(false);
  });

  it("keeps an arm that only checked entries, with no book to draw", () => {
    const tiles = armTiles([forestArm("a", 2, 50)], [rail("a", 30, 2, {}), rail("quiet", 80, 0, { window_blocked: 80 })]);
    const quiet = tiles.find((t) => t.arm === "quiet")!;
    expect(quiet.entered).toBe(0);
    expect(quiet.realised).toBeNull();
    const html = text(renderToString(<ArmTileView t={quiet} spot={null} onOpen={() => undefined} />));
    expect(html).toContain("no book to draw");
    expect(html).toContain("0 filled of 80 checks");
    expect(html).toContain("window 100%");
  });

  it("a losing arm's tile is red, a control on another scale is dashed and not ranked", () => {
    const [a, , control] = armTiles([forestArm("control", 296, 900), forestArm("a", 5, -10), forestArm("b", 6, -40)], [rail("control", 312, 296, {})]);
    const loss = renderToString(<ArmTileView t={a!} spot={5850} onOpen={() => undefined} />);
    expect(loss).toContain("is-neg");
    expect(loss).toContain("pnl-neg");
    const ctl = renderToString(<ArmTileView t={control!} spot={5850} onOpen={() => undefined} />);
    expect(ctl).toContain("is-other-scale");
    expect(text(ctl)).toContain("another scale");
    expect(text(ctl)).toContain("not ranked");
  });
});
