import fs from "node:fs";
import path from "node:path";
import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NAV_DECL } from "../src/lightbox/navGroups";
import { MODULE_LABEL, TRADING_MODULE_ORDER, isModuleId } from "../src/lightbox/moduleOrder";
import type { SuiteFeatures, SuiteFeaturesOk } from "@console/shared";

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

  it("an unknown module name still 404s, rather than opening an empty frame", () => {
    const html = render("/not-a-real-module");
    expect(html).toContain("Page not found");
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

  it("the rail starts collapsed: a toggle, with its links hidden rather than gone", () => {
    const html = render("/flies/forest");
    expect(html).toContain("mf-rail-collapsed");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toMatch(/<div id="mf-nav-links" hidden="">/);
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

  // Cards link to pages rather than opening overlays (2026-09-24). A link to a tab the rail does
  // not declare resolves to the first tab and looks like it worked, so every `to="/<page>/<tab>"`
  // in the web source is read -- not a list kept here -- and each must open its own tab. That
  // covers links across pages too (flies' live-pilot tile to Live, 2026-09-25).
  const links = new Set<string>();
  const walk = (dir: string): void => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith(".tsx")) {
        for (const m of fs.readFileSync(p, "utf-8").matchAll(/\bto="(\/([a-z]+)\/[a-z]+)"/g)) {
          if (isModuleId(m[2]!)) links.add(m[1]!);
        }
      }
    }
  };
  walk(path.join(__dirname, "..", "src"));

  it("finds card links on every trading module's pages", () => {
    // A regex that silently matched nothing would pass every check below.
    for (const module of TRADING_MODULE_ORDER) {
      expect([...links].some((l) => l.startsWith(`/${module}/`)), `no card links found for ${module}`).toBe(true);
    }
  });

  it("every page a card links to is a real tab, not the first-tab fallback", () => {
    for (const to of links) {
      const [, module, id] = to.split("/") as [string, string, string];
      if (!isModuleId(module)) continue;
      const label = NAV_DECL[module].slides.find((sl) => sl.id === id)?.label;
      expect(label, `${to} is not a declared ${module} tab`).toBeDefined();
      expect(text(render(to)), `${to} did not open its own tab`).toContain(`${MODULE_LABEL[module]} / ${label!}`);
    }
  });
});

describe("the suite surfaces are on the frame", () => {
  // GEX, Live, Reports, Advisor and Config moved off the lightbox on 2026-09-25, the last five.
  // Each renders the rail, opens out under its own name, and resolves its tab from the URL.
  const cases: Array<[string, string]> = [
    ["/gex/skew", "GEX / iv skew"],
    ["/gex", "GEX / gex"],
    ["/live", "Live / today"],
    ["/reports/eod", "Reports / eod"],
    ["/reports", "Reports / morning"],
    ["/advisor", "Advisor / advisor"],
    ["/system", "System / health"],
    ["/system/supervisor", "System / supervisor"],
    ["/config/prefs", "Config / prefs"],
    ["/config", "Config / arms"], // "arms & profiles": the ampersand is escaped in markup
  ];
  for (const [route, crumb] of cases) {
    it(`${route} renders the rail and opens on ${crumb}`, () => {
      const html = render(route);
      expect(html).toContain('aria-label="modules"');
      // NavLink marks the current tab only when the URL names it, as on every other page.
      if (route.split("/").length > 2) expect(html).toContain('aria-current="page"');
      expect(text(html)).toContain(crumb);
      expect(html).not.toContain("Page not found");
    });
  }

  it("an unknown tab on a suite surface falls back to its first tab rather than 404ing", () => {
    expect(text(render("/gex/profile"))).toContain("GEX / gex");
  });
});

describe("a page the suite has turned off", () => {
  /** Render with the features answer already in the cache, as it is after the first poll. */
  function renderWith(route: string, features: SuiteFeatures): string {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(["features"], features);
    return renderToString(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[route]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  const on = { configured: true, enabled: true, missing: [] };
  const base: SuiteFeaturesOk = {
    ok: true,
    capabilities: { claude: true, dolt: false },
    modules: {
      meic: on,
      flies: on,
      calendars: on,
      pmcc: { configured: false, enabled: false, missing: [] },
      curve: on,
      bwb: on,
      earnings: { configured: true, enabled: false, missing: ["dolt"] },
    },
    services: { "gex-recorder": true },
    features: { advisor: false, technicals: false, review_narrative: true, morning_narrative: true },
  };

  it("renders the turned-off card, not the module and not a 404", () => {
    const html = text(renderWith("/pmcc", base));
    expect(html).toContain("PMCC-99 is turned off");
    expect(html).toContain("switched off in the suite config");
    expect(html).not.toContain("Page not found");
    expect(html).not.toContain("PMCC-99 / session");
    expect(renderWith("/pmcc", base)).toContain('href="/config"');
  });

  it("says which capability a switched-on module is missing", () => {
    expect(text(renderWith("/earnings/history", base))).toContain("needs Dolt");
  });

  it("the advisor page is a turned-off card when the advisor is off", () => {
    expect(text(renderWith("/advisor", base))).toContain("Advisor is turned off");
  });

  it("a module that is on still renders, and loses its advisor tab when the advisor is off", () => {
    const html = renderWith("/flies/session", base);
    expect(text(html)).toContain("Flies / session");
    expect(html).not.toContain('href="/flies/advisor"');
    expect(html).not.toContain('href="/advisor"');
    // With the advisor on, the same tab is in the rail.
    expect(renderWith("/flies/session", { ...base, features: { ...base.features, advisor: true } })).toContain('href="/flies/advisor"');
  });

  it("the rail leaves off modules out, and Config keeps its link", () => {
    const html = renderWith("/flies/session", base);
    expect(html).not.toContain('href="/pmcc"');
    expect(html).not.toContain('href="/earnings"');
    expect(html).toContain('href="/meic"');
    expect(html).toContain('href="/config"');
  });

  it("Charts loses its technicals tab when technicals is off, and keeps the live intraday chart", () => {
    expect(renderWith("/charts", base)).not.toContain('href="/charts/technicals"');
    expect(renderWith("/charts", base)).toContain('href="/charts/intraday"');
    expect(renderWith("/charts", { ...base, features: { ...base.features, technicals: true } })).toContain('href="/charts/technicals"');
  });

  it("Reports no longer carries a chart tab", () => {
    const on = { ...base, features: { ...base.features, technicals: true } };
    expect(renderWith("/reports", on)).not.toContain('href="/reports/chart"');
  });

  it("an experimental module that is on carries the chip in its title", () => {
    // The chip follows the breadcrumb; the rail carries it beside each experimental module's link.
    expect(text(renderWith("/curve", base))).toMatch(/curve \/ session\s*experimental/);
    expect(text(renderWith("/meic", base))).not.toMatch(/MEIC \/ session\s*experimental/);
    expect(text(renderWith("/meic", base))).toMatch(/Calendars\s*experimental/);
  });

  it("a failed features read shows everything (fail open)", () => {
    const html = text(renderWith("/pmcc", { ok: false, error: "bridge down" }));
    expect(html).toContain("PMCC-99 / session");
    expect(html).not.toContain("turned off");
  });
});

describe("the old report routes", () => {
  it("/morning and /review are still routed — they do not fall through to not-found", () => {
    // `<Navigate>` redirects through a state update, which a single server-render pass never runs,
    // so the destination's content is not what comes back here. What IS verifiable is that both
    // paths match a route at all: if either redirect were dropped, the catch-all would claim it.
    for (const path of ["/morning", "/review"]) {
      expect(render(path)).not.toContain("Page not found");
    }
  });

  it("/reports/chart, the technicals chart's old home, is still routed", () => {
    // Morning-pack links and bookmarks carry `?symbol=`; the redirect keeps it (`MovedTo`).
    expect(render("/reports/chart?symbol=MSFT")).not.toContain("Page not found");
  });
});
