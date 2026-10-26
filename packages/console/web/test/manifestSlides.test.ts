import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { MODULE_ORDER, type ModuleId } from "../src/lightbox/moduleOrder";
import { navSlideIds } from "../src/lightbox/navGroups";
import { SECTIONS } from "../src/pages/Config/fieldMeta";
import { LIVE_ACCOUNT_WIDE } from "../src/lib/visibility";
import { slides as advisor } from "../src/lightbox/manifests/AdvisorLightbox";
import { slides as backtest } from "../src/lightbox/manifests/BacktestLightbox";
import { slides as charts } from "../src/lightbox/manifests/ChartsLightbox";
import { slides as compare } from "../src/lightbox/manifests/CompareLightbox";
import { slides as live } from "../src/lightbox/manifests/LiveLightbox";
import { slides as reports } from "../src/lightbox/manifests/ReportsLightbox";
import { slides as screeners } from "../src/lightbox/manifests/ScreenersLightbox";
import { slides as system } from "../src/lightbox/manifests/SystemLightbox";

/**
 * Each page's tabs are stated twice: the rail's declaration (`navGroups.ts`) and the manifest that
 * renders them. `ModuleFrame` warns at runtime about a manifest tab the rail does not declare, but
 * nothing caught the other direction -- a declared tab the manifest never renders, or a manifest
 * tab dropped from the declaration until someone opened the page. This compares the two as sets,
 * both ways, for every page in `MODULE_ORDER`.
 *
 * A manifest whose list is a module-level constant is compared through its export. The rest build
 * their list inside the component (each slide's render closes over the page's hooks), so they are
 * read from source: every `id: "..."` in the file, which today is exactly the slide list. A stray
 * `id:` added to one of those files fails here loudly, which is the safe direction.
 *
 * Shown to fail: removing `execution` from `LIVE_SLIDES` reports it as a manifest tab the rail does
 * not declare, and removing `slices` from `BACKTEST_SLIDES` or `tracker` from `PMCC_SLIDES` does the
 * same for those pages. Each was reverted.
 */
const MANIFESTS = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src", "lightbox", "manifests");

const EXPORTED: Partial<Record<ModuleId, readonly { id: string }[]>> = {
  advisor,
  backtest,
  charts,
  compare,
  live,
  reports,
  screeners,
  system,
};

function manifestFile(module: ModuleId): string {
  return path.join(MANIFESTS, `${module[0]!.toUpperCase()}${module.slice(1)}Lightbox.tsx`);
}

/** The ids a manifest renders, from its export or its source. */
function manifestIds(module: ModuleId): string[] {
  const exported = EXPORTED[module];
  if (exported !== undefined) return exported.map((s) => s.id);
  const text = fs.readFileSync(manifestFile(module), "utf8");
  const ids = [...text.matchAll(/\bid:\s*"([^"]+)"/g)].map((m) => m[1]!);
  // Config's section pages are mapped from `fieldMeta.ts`, as the declaration's are.
  return text.includes("...SECTIONS.map(") ? [...SECTIONS.map((s) => s.id), ...ids] : ids;
}

describe("every manifest renders exactly the tabs its page declares", () => {
  it("covers every manifest file, one per page", () => {
    const files = fs.readdirSync(MANIFESTS).filter((f) => f.endsWith("Lightbox.tsx")).sort();
    expect(files).toEqual(MODULE_ORDER.map((m) => path.basename(manifestFile(m))).sort());
  });

  for (const module of MODULE_ORDER) {
    it(`${module}: the manifest's tabs equal navSlideIds("${module}")`, () => {
      const ids = manifestIds(module);
      expect(new Set(ids).size).toBe(ids.length);
      expect([...ids].sort()).toEqual([...navSlideIds(module)].sort());
    });
  }
});

describe("the account-wide Live tabs", () => {
  it("are declared Live tabs", () => {
    const ids = navSlideIds("live");
    for (const id of LIVE_ACCOUNT_WIDE) expect(ids).toContain(id);
  });
});
