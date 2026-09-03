import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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
    // Both land on OverviewWithLightbox; the slide segment is read by the module's own manifest
    // once mounted (a `document`-dependent concern this pass can't see), but routing itself must
    // not treat the extra segment as unknown.
    const html = render("/flies/forest");
    expect(html).toContain("Flies");
    expect(html).not.toContain("Page not found");
  });
});

describe("the reports/gex/advisor routes", () => {
  // Reports, GEX and Advisor are suite-level surfaces given the same lightbox carousel treatment
  // as the trading modules (2026-09) -- they resolve through the same `OverviewWithLightbox` and
  // hit the same SSR-can't-render-a-portal wall the module routes describe block already covers.
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

  it("/morning and /review are still routed — they do not fall through to not-found", () => {
    // `<Navigate>` redirects through a state update, which a single server-render pass never runs,
    // so the destination's content is not what comes back here. What IS verifiable is that both
    // paths match a route at all: if either redirect were dropped, the catch-all would claim it.
    for (const path of ["/morning", "/review"]) {
      expect(render(path)).not.toContain("Page not found");
    }
  });
});
