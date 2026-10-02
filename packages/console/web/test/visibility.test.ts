import { describe, it, expect } from "vitest";
import type { SuiteFeatures, SuiteFeaturesOk } from "@console/shared";
import { isFeatureOn, isModuleVisible, isSlideVisible, offReason } from "../src/lib/visibility";
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
  services: { "gex-recorder": true, "meic-sidecar": false },
  features: { advisor: true, review_narrative: true, morning_narrative: true, technicals: true },
};

function withOff(over: Partial<SuiteFeaturesOk>): SuiteFeaturesOk {
  return { ...ALL_ON, ...over };
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

  it("Reports' chart follows technicals; the narratives follow their own features", () => {
    const off = withOff({ features: { advisor: true, technicals: false, review_narrative: false, morning_narrative: true } });
    expect(isSlideVisible("reports", "chart", off)).toBe(false);
    expect(isSlideVisible("reports", "morning", off)).toBe(true);
    expect(isSlideVisible("reports", "chart", ALL_ON)).toBe(true);
    expect(isFeatureOn("technicals", off)).toBe(false);
    expect(isFeatureOn("review_narrative", off)).toBe(false);
    expect(isFeatureOn("morning_narrative", off)).toBe(true);
  });

  it("Config, System, Live, Reports and the Overview are never hidden", () => {
    const everythingOff = withOff({ modules: {}, services: { "gex-recorder": false }, features: { advisor: false, technicals: false } });
    for (const id of ["config", "system", "live", "reports", ""]) expect(isModuleVisible(id, everythingOff)).toBe(true);
  });
});

describe("fail open", () => {
  it("unknown features (still loading) show everything", () => {
    for (const id of ["earnings", "gex", "advisor"]) expect(isModuleVisible(id, undefined)).toBe(true);
    expect(isSlideVisible("flies", "advisor", undefined)).toBe(true);
    expect(isFeatureOn("technicals", undefined)).toBe(true);
    expect(offReason("earnings", undefined)).toBeNull();
    expect(visibleNavLinks(undefined).modules).toHaveLength(8);
  });

  it("a failed bridge shows everything", () => {
    const failed: SuiteFeatures = { ok: false, error: "config bridge unavailable" };
    for (const id of ["earnings", "gex", "advisor"]) expect(isModuleVisible(id, failed)).toBe(true);
    expect(isFeatureOn("advisor", failed)).toBe(true);
    expect(isSlideVisible("reports", "chart", failed)).toBe(true);
    expect(offReason("earnings", failed)).toBeNull();
    expect(visibleNavLinks(failed).modules).toHaveLength(8);
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
  it("drop an off module and renumber the digits over what is left", () => {
    const { modules } = visibleNavLinks(pmccSwitchedOff);
    expect(modules.map((l) => [l.to, l.key])).toEqual([
      ["/meic", "1"],
      ["/flies", "2"],
      ["/curve", "3"],
      ["/bwb", "4"],
      ["/calendars", "5"],
      ["/earnings", "6"],
      ["/gex", "7"],
    ]);
  });

  it("drop the advisor from the suite links when it is off", () => {
    const off = withOff({ features: { advisor: false } });
    expect(visibleNavLinks(off).suite.map((l) => l.to)).not.toContain("/advisor");
    expect(visibleNavLinks(ALL_ON).suite.map((l) => l.to)).toContain("/advisor");
  });
});
