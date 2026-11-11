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

describe("Config broker logins", () => {
  // An array, edited by index and labelled by name: the order in the file is the index written.
  const doc = {
    broker: { logins: [{ name: "margin", label: "Reg T", manage: true }, { label: "no name" }, { name: "ira2", manage: false }] },
    streamer: { enabled: true, instances: ["margin"] },
  };

  it("offers each login's label and switches by its index, named, never its name", () => {
    const out = resolveSection("modules", { orchestrator: target(doc) } as never);
    const fields = out.find((g) => g.target === "orchestrator")?.fields ?? [];
    const logins = fields.filter((f) => f.pointer.startsWith("/broker/logins/"));
    expect(logins.map((f) => [f.label, f.pointer, f.value])).toEqual([
      ["margin", "/broker/logins/0/label", "Reg T"],
      ["margin", "/broker/logins/0/manage", true],
      ["ira2", "/broker/logins/2/manage", false],
    ]);
    expect(fields.some((f) => f.pointer.endsWith("/name"))).toBe(false);
    expect(fields.find((f) => f.pointer === "/streamer/instances")?.value).toEqual(["margin"]);
  });

  it("offers nothing for logins when the config lists none", () => {
    const out = resolveSection("modules", { orchestrator: target({ modules: {} }) } as never);
    const fields = out.find((g) => g.target === "orchestrator")?.fields ?? [];
    expect(fields.filter((f) => f.pointer.startsWith("/broker"))).toEqual([]);
  });
});
