import { describe, it, expect } from "vitest";
import { resolveSection } from "../src/pages/Config/fieldMeta";
import type { ConfigTargetModel } from "@console/shared";

/**
 * The Config page's arm toggles are enumerated from the document, not from a list, so they depend
 * on finding the arm registry by name. That name has three accepted spellings across the suite
 * (`arms`, `books`, `profiles` — see cherrypick.core.config), and an operator's config file is
 * kept across upgrades rather than migrated.
 *
 * The failure this guards is silent in the worst way: a registry the page cannot find yields no
 * toggles at all, which renders as a module that simply has no arms to switch — indistinguishable
 * from a module that has none.
 */
function target(doc: Record<string, unknown>): ConfigTargetModel {
  return { exists: true, doc, mtime: 1, guarded: [], issues: [] };
}

const ARMS = { control: { enabled: true }, delta: { enabled: false }, _note: "docs-as-data" };

describe("Config arm toggles", () => {
  it.each(["arms", "books", "profiles"])("finds the flies registry spelled %s", (key) => {
    const out = resolveSection("arms", { flies: target({ [key]: ARMS }) } as never);
    const flies = out.find((g) => g.target === "flies");

    expect(flies?.fields.map((f) => f.label)).toEqual(["control", "delta"]);
    expect(flies?.fields[0]?.pointer).toBe(`/${key}/control/enabled`);
    expect(flies?.fields[0]?.value).toBe(true);
  });

  it("offers nothing when the registry is spelled a way nothing reads", () => {
    const out = resolveSection("arms", { flies: target({ variants: ARMS }) } as never);
    expect(out.find((g) => g.target === "flies")).toBeUndefined();
  });

  it("skips the configs' docs-as-data keys rather than offering them as arms", () => {
    const out = resolveSection("arms", { flies: target({ arms: ARMS }) } as never);
    expect(out.find((g) => g.target === "flies")?.fields.map((f) => f.label)).not.toContain("_note");
  });
});
