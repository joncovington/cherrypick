import fs from "node:fs";
import path from "node:path";
import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NAV_DECL } from "../src/lightbox/navGroups";
import { FRAME_MODULE_IDS, type FrameModuleId } from "../src/lightbox/registry";
import { MODULE_LABEL } from "../src/lightbox/moduleOrder";

/** Where each frame module's pages live, for the card-link check below. */
const PAGE_DIR: Record<FrameModuleId, string> = { flies: "Flies", meic: "Meic", bwb: "Bwb", earnings: "Earnings", curve: "Curve", pmcc: "Pmcc" };

/**
 * Route wiring, rendered rather than read.
 *
 * The case that matters is the catch-all. `<Routes>` with nothing matching renders an empty string,
 * so before this existed an unknown path was a blank screen — no error, no message, no way back,
 * and indistinguishable from a crashed app. That is exactly what a tab left open across a rebuild
 * sees when it asks the old bundle for a route only the new one has.
 *
 * The shell's three client-only stores are stubbed here rather than given server snapshots in
 * production code. They are genuinely browser-only by design — a localStorage mirror and a
 * WebSocket — and `useSyncExternalStore` has no meaningful server value for either, so teaching
 * them one to satisfy a renderer they never run under would be a change to shipping code made for
 * the test's convenience. Stubbing keeps that pressure inside the test file.
 */

vi.mock("../src/lib/prefs", () => ({
  useBoolPref: () => false,
  usePrefsSync: () => undefined,
  usePrefsVersion: () => 0,
  writePref: async () => undefined,
}));
vi.mock("../src/pages/Config/stagedStore", () => ({
  useDirtyCount: () => 0,
  useStagedVersion: () => 0,
}));
vi.mock("../src/lib/useQuote", () => ({
  useQuote: () => undefined,
  useWsState: () => "closed",
}));

const { default: App } = await import("../src/App");

/** The rendered text, as a reader (and `ui-check --expect`) sees it, rather than as markup. */
function text(html: string): string {
  return html
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function render(path: string): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToString(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("no path renders a blank page", () => {
  it("an unknown path gets the not-found page, never an empty document", () => {
    const html = render("/no-such-page");
    expect(html).toContain("Page not found");
    expect(html).toContain("/no-such-page");
  });

  it("the not-found page offers a way back", () => {
    const html = render("/no-such-page");
    expect(html).toContain('href="/reports"');
  });
});

describe("the module routes", () => {
  // Each module now opens as a lightbox carousel over the Overview (`OverviewWithLightbox`),
  // portalled via `createPortal(..., document.body)` -- there is no `document` in this
  // server-render pass, so `LightboxFrame` deliberately renders null here (see its own comment)
  // rather than throwing. What IS verifiable without a browser: the route resolves to a real
  // module (not the 404 catch-all) and the header menu names it. The carousel's own content is a
  // `pnpm ui-check` concern, covered per-module there.
  it("/pmcc resolves to the pmcc module, not the catch-all", () => {
    const html = render("/pmcc");
    expect(html).toContain("PMCC");
    expect(html).not.toContain("Page not found");
  });

  it("/calendars resolves to the calendars module, not the catch-all", () => {
    const html = render("/calendars");
    expect(html).toContain("Calendars");
    expect(html).not.toContain("Page not found");
  });

  it("an unknown module name still 404s, rather than opening an empty carousel", () => {
    const html = render("/not-a-real-module");
    expect(html).toContain("Page not found");
  });

  it("a deep-linked slide resolves the same as the bare module route", () => {
    // Both land on the same route element; the slide segment is read there (frame) or by the
    // module's own manifest once mounted (lightbox), but routing itself must not treat the extra
    // segment as unknown.
    const html = render("/gex/profile");
    expect(html).toContain("GEX");
    expect(html).not.toContain("Page not found");
  });
});

describe("the module frame", () => {
  /**
   * Flies renders the frame rather than a lightbox, and the rail and breadcrumb sit OUTSIDE the
   * lazy manifest specifically so a server render can see them — React emits the Suspense
   * fallback for an unresolved `lazy()` rather than resolving it, so anything inside the boundary
   * is invisible to every test this package has. Which tab a URL resolves to is therefore the one
   * thing that IS checkable here, and it is most of what there is to get wrong.
   */
  it("renders the rail, and marks the tab the URL named", () => {
    const html = render("/flies/forest");
    expect(html).toContain('aria-label="modules"');
    expect(html).toContain('aria-current="page"');
    expect(text(html)).toContain("Flies / forest");
  });

  it("the bare module route opens on the first tab", () => {
    expect(text(render("/flies"))).toContain("Flies / session");
  });

  it("an unknown slide falls back to the first tab rather than 404ing", () => {
    const html = render("/flies/no-such-slide");
    expect(text(html)).toContain("Flies / session");
    expect(html).not.toContain("Page not found");
  });

  it("a renamed tab's old id reaches its replacement, not the first tab", () => {
    // The dangerous failure is the quiet one: `/flies/exits` falling through to `session` would
    // look like a working link while showing the wrong page. A 404 would at least be visible.
    expect(text(render("/flies/exits"))).toContain("Flies / divergence");
    expect(text(render("/flies/calibration"))).toContain("Flies / completion");
    expect(text(render("/flies/journal"))).toContain("Flies / decisions");
    expect(text(render("/flies/openrange"))).toContain("Flies / opening range");
    expect(text(render("/flies/now"))).toContain("Flies / session");
    expect(text(render("/flies/trades"))).toContain("Flies / positions");
  });

  it("MEIC's renamed tabs reach their replacements", () => {
    expect(text(render("/meic"))).toContain("MEIC / session");
    expect(text(render("/meic/now"))).toContain("MEIC / session");
    expect(text(render("/meic/trades"))).toContain("MEIC / history");
    expect(text(render("/meic/sessions"))).toContain("MEIC / sessions");
  });

  // Cards link to module pages rather than opening overlays (2026-09-24). A link to a tab the rail
  // does not declare resolves to the first tab and looks like it worked, so the links are read from
  // each frame module's own page sources -- not a list kept here -- and each must open its own tab.
  // Driven off FRAME_MODULE_IDS, so a module that moves onto the frame is covered the day it moves.
  for (const module of FRAME_MODULE_IDS) {
    it(`every page a ${module} card links to is a real tab, not the first-tab fallback`, () => {
      const pagesDir = path.join(__dirname, "..", "src", "pages", PAGE_DIR[module]);
      const targets = new Set<string>();
      const pattern = new RegExp(`\\bto="(\\/${module}\\/[a-z]+)"`, "g");
      for (const f of fs.readdirSync(pagesDir).filter((n) => n.endsWith(".tsx"))) {
        for (const m of fs.readFileSync(path.join(pagesDir, f), "utf-8").matchAll(pattern)) targets.add(m[1]!);
      }
      expect(targets.size).toBeGreaterThan(0);
      const slides = NAV_DECL[module]!.slides;
      for (const to of targets) {
        const id = to.split("/")[2]!;
        const label = slides.find((s) => s.id === id)?.label;
        expect(label, `${to} is not a declared ${module} tab`).toBeDefined();
        expect(text(render(to)), `${to} did not open its own tab`).toContain(`${MODULE_LABEL[module]} / ${label!}`);
      }
    });
  }

  it("a lightbox module grew no rail — the two shapes stay apart", () => {
    const html = render("/gex");
    expect(html).toContain("GEX");
    expect(html).not.toContain('aria-label="modules"');
    expect(html).not.toContain("mf-nav");
  });
});

describe("the reports/gex/advisor/config routes", () => {
  // Reports, GEX, Advisor and Config are suite-level surfaces given the same lightbox carousel
  // treatment as the trading modules (2026-09) -- they resolve through the same
  // `OverviewWithLightbox` and hit the same SSR-can't-render-a-portal wall the module routes
  // describe block already covers.
  it("/reports resolves to the reports lightbox, not the catch-all", () => {
    const html = render("/reports");
    expect(html).toContain("Reports");
    expect(html).not.toContain("Page not found");
  });

  it("/gex resolves to the gex lightbox, not the catch-all", () => {
    const html = render("/gex");
    expect(html).toContain("GEX");
    expect(html).not.toContain("Page not found");
  });

  it("/advisor resolves to the advisor lightbox, not the catch-all", () => {
    const html = render("/advisor");
    expect(html).toContain("Advisor");
    expect(html).not.toContain("Page not found");
  });

  it("/config resolves to the config lightbox, not the catch-all", () => {
    const html = render("/config");
    expect(html).toContain("Config");
    expect(html).not.toContain("Page not found");
  });

  it("/morning and /review are still routed — they do not fall through to not-found", () => {
    // `<Navigate>` redirects through a state update, which a single server-render pass never runs,
    // so the destination's content is not what comes back here. What IS verifiable is that both
    // paths match a route at all: if either redirect were dropped, the catch-all would claim it.
    for (const path of ["/morning", "/review"]) {
      expect(render(path)).not.toContain("Page not found");
    }
  });
});
