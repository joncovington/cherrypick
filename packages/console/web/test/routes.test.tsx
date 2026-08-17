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
  it("/pmcc renders its own page, not the catch-all", () => {
    // Server-rendered with no query data, so what is asserted is the shell the page paints before
    // anything loads. The module is paper-only by construction — there is no live store to toggle
    // to — so the badge is fixed rather than driven by a mode preference.
    const html = render("/pmcc");
    expect(html).toContain("PMCC-99");
    expect(html).not.toContain("Page not found");
  });

  it("/calendars renders its own page, not the catch-all", () => {
    // Paper-only by construction for the same structural reason as PMCC: no live loop, no live
    // store, so nothing a mode toggle could reach and a fixed badge rather than a preference.
    const html = render("/calendars");
    expect(html).toContain("Calendars");
    expect(html).not.toContain("Page not found");
  });
});

describe("the reports routes", () => {
  it("/reports renders the morning report", () => {
    expect(render("/reports")).toContain("Morning report");
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
