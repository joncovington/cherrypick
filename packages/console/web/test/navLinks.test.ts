import { describe, expect, it } from "vitest";
import { CONFIG_LINK, EXPERIMENT_LINKS, MODULE_LINKS, SUITE_LINKS, railHalves } from "../src/components/shell/navLinks";
import { SUITE_ORDER, TRADING_MODULE_ORDER } from "../src/lightbox/moduleOrder";

/**
 * The header menu and the rail read `MODULE_LINKS`, a hand-kept list; the routes read
 * `TRADING_MODULE_ORDER`. contango shipped 2026-10-05 in the second and not the first: its pages
 * rendered at /contango and nothing linked to them. Driven off the module order, so the next
 * module added there fails here until it has a link.
 */
describe("module nav links", () => {
  it("links every trading module: the live-capable ones on the rail, the rest through Experiments", () => {
    const linked = [...MODULE_LINKS, ...EXPERIMENT_LINKS].map((l) => l.to.slice(1)).filter((id) => id !== "experiments");
    expect([...linked].sort()).toEqual([...TRADING_MODULE_ORDER].sort());
    expect(MODULE_LINKS.map((l) => l.to)).toEqual(["/meic", "/bwb", "/earnings", "/experiments"]);
  });

  it("gives every link a distinct shortcut, Config's included", () => {
    const keys = MODULE_LINKS.map((l) => l.key);
    expect(keys).toEqual(["1", "2", "3", "4"]);
    const chords = [...SUITE_LINKS, CONFIG_LINK].map((l) => l.key);
    expect(new Set(chords).size).toBe(chords.length);
    expect(CONFIG_LINK.key).toBe("o");
  });
});

describe("the rail's order (2026-10-18)", () => {
  it("puts real money first and the market reads above the modules", () => {
    const { above } = railHalves(SUITE_LINKS);
    expect(above.map((l) => l.to)).toEqual(["/live", "/reports", "/charts", "/flow", "/gex", "/screeners"]);
  });

  it("puts research and the suite's own pages below the modules", () => {
    const { below } = railHalves(SUITE_LINKS);
    expect(below.map((l) => l.to)).toEqual(["/compare", "/backtest", "/advisor", "/system"]);
  });

  it("lists the suite pages in the frame's own order, Config last", () => {
    // Experiments sits in the Modules section, not among the suite pages (2026-10-30).
    expect(SUITE_LINKS.map((l) => l.to.slice(1))).toEqual(SUITE_ORDER.filter((id) => id !== "config" && id !== "experiments"));
  });

  it("leads the modules with the live-capable ones, flies first", () => {
    expect(TRADING_MODULE_ORDER.slice(0, 4)).toEqual(["flies", "meic", "bwb", "earnings"]);
  });
});
