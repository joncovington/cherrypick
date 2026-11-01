import { describe, it, expect } from "vitest";
import { settingsLine } from "../src/pages/Backtest/RunCards";

/**
 * The run cards' settings strip (2026-11-01): what decided an arm's trades on one line, a grid arm
 * read as the setting it changes from control. The diff is `cards.arm_settings`'; the page only
 * words it. Synthetic settings only.
 */

const base = { recorded: true as const, cadence: 60, fill_model: "meic-paper-mid-haircut" };

describe("the settings strip", () => {
  it("reads a grid arm as the setting it changes from control", () => {
    expect(
      settingsLine("d20", { ...base, base: "control", differs: { short_delta_target: { arm: 0.2, base: 0.16 } } }),
    ).toBe("d20: 60 s ticks · fill model meic-paper-mid-haircut · vs control: short_delta_target 0.2 (control 0.16)");
  });

  it("names control as the base and an arm with nothing changed as the same", () => {
    expect(settingsLine("control", { ...base, base: null, differs: {} })).toBe(
      "control: 60 s ticks · fill model meic-paper-mid-haircut · the base every other arm is read against",
    );
    expect(settingsLine("copy", { ...base, base: "control", differs: {} })).toContain("same settings as control");
  });

  it("says when settings were not recorded, shows an absent param as unset, and caps a long diff", () => {
    expect(settingsLine("old", { recorded: false })).toBe("old: settings not recorded for this run.");
    expect(settingsLine("g", { ...base, base: "control", differs: { gate: { arm: "vix", base: null } } })).toContain("gate vix (control unset)");
    const many = Object.fromEntries(Array.from({ length: 9 }, (_, i) => [`p${String(i)}`, { arm: i, base: 0 }]));
    expect(settingsLine("wide", { ...base, base: "control", differs: many })).toMatch(/, \+3 more$/);
    expect(settingsLine("none", undefined)).toBeNull();
  });
});
