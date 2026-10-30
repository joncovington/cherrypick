import { describe, it, expect } from "vitest";
import type { LiveAttentionItem, LiveTodayLoop, LiveTodayPayload, SuiteFeatures, SuiteFeaturesOk } from "@console/shared";
import { isFeatureOn, isHiddenAsUnknown, isModuleVisible, isSlideVisible, liveLoopsInUse, offReason, screenerOffReason } from "../src/lib/visibility";
import { visibleNavLinks } from "../src/components/shell/navLinks";

/**
 * What the console hides when the suite turns something off. Every rule reads the orchestrator's
 * `features` answer and nothing else; every rule shows the thing when that answer is unknown.
 */

const ALL_ON: SuiteFeaturesOk = {
  ok: true,
  capabilities: { claude: true, dolt: true },
  modules: Object.fromEntries(
    ["meic", "flies", "calendars", "pmcc", "curve", "bwb", "earnings"].map((m) => [m, { configured: true, enabled: true, missing: [] }]),
  ),
  services: { "gex-recorder": true },
  features: { advisor: true, review_narrative: true, morning_narrative: true, technicals: true },
};

function withOff(over: Partial<SuiteFeaturesOk>): SuiteFeaturesOk {
  return { ...ALL_ON, ...over };
}

/** A Live Today answer carrying only what the visibility rule reads; every loop idle unless told. */
function today(loops: Array<Partial<LiveTodayLoop> & { loop: LiveTodayLoop["loop"] }>, attention: LiveAttentionItem[] = []): LiveTodayPayload {
  const idle = { open: 0, working: 0, held: 0, orphans: { at: null, count: 0 }, overdue: [] };
  return { loops: loops.map((l) => ({ ...idle, ...l })), attention } as unknown as LiveTodayPayload;
}

const earningsNeedsDolt = withOff({
  modules: { ...ALL_ON.modules, earnings: { configured: true, enabled: false, missing: ["dolt"] } },
});
const pmccSwitchedOff = withOff({
  modules: { ...ALL_ON.modules, pmcc: { configured: false, enabled: false, missing: [] } },
});

describe("a trading module", () => {
  it("is shown when the suite says enabled", () => {
    expect(isModuleVisible("meic", ALL_ON)).toBe(true);
  });

  it("is hidden when the suite says not enabled, whatever the reason", () => {
    expect(isModuleVisible("earnings", earningsNeedsDolt)).toBe(false);
    expect(isModuleVisible("pmcc", pmccSwitchedOff)).toBe(false);
    expect(isModuleVisible("meic", pmccSwitchedOff)).toBe(true);
  });

  it("is shown when the reply does not name it", () => {
    const rest = { ...ALL_ON.modules };
    delete rest["meic"];
    expect(isModuleVisible("meic", withOff({ modules: rest }))).toBe(true);
  });
});

describe("gex, the advisor and the reports", () => {
  it("gex follows the gex-recorder service", () => {
    expect(isModuleVisible("gex", ALL_ON)).toBe(true);
    expect(isModuleVisible("gex", withOff({ services: { "gex-recorder": false } }))).toBe(false);
  });

  it("the advisor page and every module's advisor tab follow the advisor feature", () => {
    const off = withOff({ features: { ...ALL_ON.features, advisor: false } });
    expect(isModuleVisible("advisor", off)).toBe(false);
    expect(isSlideVisible("flies", "advisor", off)).toBe(false);
    expect(isSlideVisible("meic", "advisor", off)).toBe(false);
    expect(isSlideVisible("flies", "session", off)).toBe(true);
    expect(isSlideVisible("flies", "advisor", ALL_ON)).toBe(true);
  });

  it("a module's advisor tab goes while the advisor is paused; the Advisor page stays", () => {
    const paused = withOff({ features: { ...ALL_ON.features, advisor_paused: true } });
    expect(isSlideVisible("meic", "advisor", paused)).toBe(false);
    expect(isSlideVisible("bwb", "advisor", paused)).toBe(false);
    expect(isModuleVisible("advisor", paused)).toBe(true);
    expect(isSlideVisible("meic", "advisor", withOff({ features: { ...ALL_ON.features, advisor_paused: false } }))).toBe(true);
  });

  it("a switched-off live loop's tab stays while Live Today reports anything for it (#422)", () => {
    const meicOff = withOff({ live: { flies: true, meic: false } });
    // MEIC's switch turned off mid-session with a condor still open: the only page that shows it
    // is this tab, and Live Today's row and attention items link to it.
    expect(isSlideVisible("live", "meic", meicOff, liveLoopsInUse(today([{ loop: "meic", open: 1 }])))).toBe(true);
    for (const busy of [{ working: 2 }, { held: 1 }, { orphans: { at: null, count: 1 } }, { overdue: [{ session: "2026-10-16", positions: 1, pendingEntries: 0 }] }]) {
      expect(isSlideVisible("live", "meic", meicOff, liveLoopsInUse(today([{ loop: "meic", ...busy }])))).toBe(true);
    }
    // Any attention item for the loop keeps it too (a tripped breaker, an unreadable ledger).
    const attention: LiveAttentionItem[] = [{ loop: "meic", kind: "ledger_unreadable", detail: "x", to: "/live/meic" }];
    expect(isSlideVisible("live", "meic", meicOff, liveLoopsInUse(today([{ loop: "meic" }], attention)))).toBe(true);
    // Another loop's activity is not this one's.
    expect(isSlideVisible("live", "meic", meicOff, liveLoopsInUse(today([{ loop: "meic" }, { loop: "flies", open: 3 }])))).toBe(false);
    // Idle, and read: hidden. Unread (loading, failed): unknown, so shown.
    expect(isSlideVisible("live", "meic", meicOff, liveLoopsInUse(today([{ loop: "meic" }])))).toBe(false);
    expect(isSlideVisible("live", "meic", meicOff, liveLoopsInUse(undefined))).toBe(true);
  });

  it("the account-wide pmcc tab never follows the paper pmcc module's live switch", () => {
    // The paper module's config has no live gate, so the features reply names it false. The Live tab
    // shares the id but reads the real account's evaluation, and stays like positions and the wheel.
    const paperOnly = withOff({ live: { flies: true, meic: true, pmcc: false } });
    expect(isSlideVisible("live", "pmcc", paperOnly, liveLoopsInUse(today([{ loop: "flies" }])))).toBe(true);
    expect(isSlideVisible("live", "pmcc", paperOnly)).toBe(true);
    expect(isSlideVisible("live", "wheel", withOff({ live: { flies: false, meic: false } }))).toBe(true);
  });

  it("an idle live loop's Live tab follows its live switch; unread or absent stays shown", () => {
    const fliesOff = withOff({ live: { flies: false, meic: true } });
    expect(isSlideVisible("live", "flies", fliesOff)).toBe(false);
    expect(isSlideVisible("live", "meic", fliesOff)).toBe(true);
    expect(isSlideVisible("live", "meic", withOff({ live: { meic: false } }))).toBe(false);
    // The account-wide tabs are no module's: never hidden by a switch.
    expect(isSlideVisible("live", "today", withOff({ live: { flies: false, meic: false } }))).toBe(true);
    expect(isSlideVisible("live", "positions", withOff({ live: { flies: false, meic: false } }))).toBe(true);
    // A reply that does not name the switch, an older reply with no map, and a failed bridge: shown.
    expect(isSlideVisible("live", "flies", withOff({ live: { meic: false } }))).toBe(true);
    expect(isSlideVisible("live", "flies", ALL_ON)).toBe(true);
    expect(isSlideVisible("live", "flies", { ok: false, error: "x" })).toBe(true);
    expect(isSlideVisible("live", "flies", undefined)).toBe(true);
    // The switch is about the Live tab, not the module's own page.
    expect(isSlideVisible("flies", "session", fliesOff)).toBe(true);
  });

  it("Options flow follows the options_flow feature, and is shown while the reply does not name it", () => {
    expect(isModuleVisible("flow", withOff({ features: { ...ALL_ON.features, options_flow: false } }))).toBe(false);
    expect(isModuleVisible("flow", withOff({ features: { ...ALL_ON.features, options_flow: true } }))).toBe(true);
    expect(isModuleVisible("flow", ALL_ON)).toBe(true);
  });

  it("the technicals chart follows technicals; the narratives follow their own features", () => {
    const off = withOff({ features: { advisor: true, technicals: false, review_narrative: false, morning_narrative: true } });
    expect(isSlideVisible("charts", "technicals", off)).toBe(false);
    // The live intraday chart is the console's own feed: technicals being off does not hide it.
    expect(isSlideVisible("charts", "intraday", off)).toBe(true);
    expect(isSlideVisible("reports", "morning", off)).toBe(true);
    expect(isSlideVisible("charts", "technicals", ALL_ON)).toBe(true);
    expect(isFeatureOn("technicals", off)).toBe(false);
    expect(isFeatureOn("review_narrative", off)).toBe(false);
    expect(isFeatureOn("morning_narrative", off)).toBe(true);
  });

  it("Config, System, Live, Charts, Reports and the root path are never hidden", () => {
    const everythingOff = withOff({ modules: {}, services: { "gex-recorder": false }, features: { advisor: false, technicals: false } });
    for (const id of ["config", "system", "live", "charts", "reports", ""]) expect(isModuleVisible(id, everythingOff)).toBe(true);
  });
});

describe("fail open", () => {
  it("unknown features (still loading) show everything", () => {
    for (const id of ["earnings", "gex", "advisor"]) expect(isModuleVisible(id, undefined)).toBe(true);
    expect(isSlideVisible("flies", "advisor", undefined)).toBe(true);
    expect(isFeatureOn("technicals", undefined)).toBe(true);
    expect(offReason("earnings", undefined)).toBeNull();
    expect(visibleNavLinks(undefined).modules).toHaveLength(4);
    expect(visibleNavLinks(undefined).experiments).toHaveLength(5);
    expect(visibleNavLinks(undefined).suite.map((l) => l.to)).toContain("/gex");
  });

  it("a failed bridge shows everything", () => {
    const failed: SuiteFeatures = { ok: false, error: "config bridge unavailable" };
    for (const id of ["earnings", "gex", "advisor"]) expect(isModuleVisible(id, failed)).toBe(true);
    expect(isFeatureOn("advisor", failed)).toBe(true);
    expect(isSlideVisible("charts", "technicals", failed)).toBe(true);
    expect(offReason("earnings", failed)).toBeNull();
    expect(visibleNavLinks(failed).modules).toHaveLength(4);
    expect(visibleNavLinks(failed).experiments).toHaveLength(5);
  });
});

describe("why a module is off", () => {
  it("names the missing capability for a module the user switched on", () => {
    expect(offReason("earnings", earningsNeedsDolt)?.reason).toContain("needs Dolt");
    const needsClaude = withOff({ modules: { ...ALL_ON.modules, meic: { configured: true, enabled: false, missing: ["claude"] } } });
    expect(offReason("meic", needsClaude)?.reason).toContain("needs Claude Code");
  });

  it("says switched off when the user turned it off", () => {
    expect(offReason("pmcc", pmccSwitchedOff)?.reason).toContain("switched off");
  });

  it("is null for a shown module", () => {
    expect(offReason("meic", earningsNeedsDolt)).toBeNull();
  });
});

describe("the nav lists", () => {
  it("drop an off module, keep every other module's digit, and list the experiments apart", () => {
    // Fixed keys since 2026-10-30: nothing renumbers.
    const { modules, experiments } = visibleNavLinks(pmccSwitchedOff);
    expect(modules.map((l) => [l.to, l.key])).toEqual([
      ["/meic", "1"],
      ["/bwb", "2"],
      ["/earnings", "3"],
      ["/experiments", "4"],
    ]);
    expect(experiments.map((l) => l.to)).toEqual(["/calendars", "/curve", "/contango", "/flies"]);
  });

  it("drop the advisor from the suite links when it is off", () => {
    const off = withOff({ features: { advisor: false } });
    expect(visibleNavLinks(off).suite.map((l) => l.to)).not.toContain("/advisor");
    expect(visibleNavLinks(ALL_ON).suite.map((l) => l.to)).toContain("/advisor");
  });
});

/**
 * The one page that FAILS CLOSED: Backtest shows results computed from licensed vendor history the
 * licence forbids redistributing, so every unknown hides it. Shown to fail: with the rule flipped to
 * the fail-open form every other page uses (`!known(f) || f.features.backtest !== false`), the
 * undefined, failed and missing-key cases below all came back visible.
 */
describe("backtest fails closed", () => {
  const failed: SuiteFeatures = { ok: false, error: "config bridge unavailable" };
  const missing = withOff({ features: { ...ALL_ON.features } });
  const off = withOff({ features: { ...ALL_ON.features, backtest: false } });
  const on = withOff({ features: { ...ALL_ON.features, backtest: true } });
  const hidden: Array<[string, SuiteFeatures | undefined]> = [
    ["still loading", undefined],
    ["the bridge failed", failed],
    ["the reply does not name it", missing],
    ["the suite says false", off],
  ];

  for (const [why, f] of hidden) {
    it(`is hidden when ${why}`, () => {
      expect(isModuleVisible("backtest", f)).toBe(false);
      expect(isHiddenAsUnknown("backtest", f)).toBe(true);
      // No off card and no reason: nothing on screen may say the page exists.
      expect(offReason("backtest", f)).toBeNull();
      // Its tabs go with it, so neither the rail nor j/k can name one.
      for (const slide of ["data", "validation", "runs"]) expect(isSlideVisible("backtest", slide, f)).toBe(false);
      expect(visibleNavLinks(f).suite.map((l) => l.to)).not.toContain("/backtest");
    });
  }

  it("is shown only for an explicit true", () => {
    expect(isModuleVisible("backtest", on)).toBe(true);
    expect(isHiddenAsUnknown("backtest", on)).toBe(false);
    expect(offReason("backtest", on)).toBeNull();
    expect(isSlideVisible("backtest", "runs", on)).toBe(true);
    expect(visibleNavLinks(on).suite.map((l) => l.to)).toContain("/backtest");
  });

  it("leaves every other page's fail-open rule alone", () => {
    for (const f of [undefined, failed]) {
      for (const id of ["earnings", "gex", "advisor", "flow", "system"]) {
        expect(isModuleVisible(id, f)).toBe(true);
        expect(isHiddenAsUnknown(id, f)).toBe(false);
      }
    }
  });
});

describe("a Dolt-backed screener tab", () => {
  const noDolt = withOff({ capabilities: { claude: true, dolt: false }, features: { ...ALL_ON.features, technicals: false } });
  const switchedOff = withOff({ features: { ...ALL_ON.features, technicals: false } });

  it("says it needs Dolt when the machine has none, and is never hidden", () => {
    for (const slide of ["earnings", "earnings-record", "board", "skate", "credit-spreads"]) {
      expect(screenerOffReason(slide, noDolt)).toMatchObject({ needs: "dolt" });
      expect(isSlideVisible("screeners", slide, noDolt)).toBe(true);
    }
  });

  it("says the switch is off when Dolt is there but technicals is not", () => {
    const off = screenerOffReason("board", switchedOff);
    expect(off?.needs).toBeUndefined();
    expect(off?.reason).toContain("technicals.enabled");
  });

  it("has nothing to say when technicals is on, for the income lists, or when the answer is unknown", () => {
    expect(screenerOffReason("earnings", ALL_ON)).toBeNull();
    expect(screenerOffReason("short-puts", noDolt)).toBeNull();
    expect(screenerOffReason("earnings", undefined)).toBeNull();
    expect(screenerOffReason("earnings", { ok: false, error: "bridge down" } as SuiteFeatures)).toBeNull();
  });
});
