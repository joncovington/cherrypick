import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import type { SuiteFeaturesOk } from "@console/shared";

/**
 * The command palette, rendered. What is worth pinning here is what the hamburger menu's legend
 * could not promise: the list is built from what the suite DECLARES (not a hand-kept copy), an
 * off module is not offered, the current module's tabs are offered with the page's query riding
 * along, and the shortcut hints are present — the reason the palette exists at all is that the
 * menu that used to print them is gone.
 */

vi.mock("../src/lib/prefs", () => ({
  useBoolPref: () => false,
  usePrefsSync: () => undefined,
  usePrefsVersion: () => 0,
  writePref: async () => undefined,
  getPref: () => undefined,
}));
vi.mock("../src/lib/useFeatures", () => ({
  useFeatures: () => undefined,
}));

const { CommandPalette } = await import("../src/components/shell/CommandPalette");
const { setPaletteOpen } = await import("../src/components/shell/paletteStore");

function text(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function render(route: string): { html: string; text: string } {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  setPaletteOpen(true);
  const html = renderToString(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <CommandPalette />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  setPaletteOpen(false);
  return { html, text: text(html) };
}

describe("the command palette", () => {
  it("closed renders nothing at all", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const html = renderToString(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/flies"]}>
          <CommandPalette />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(html).toBe("");
  });

  it("names every suite page, the modules, and the current module's tabs — the rail's own list", () => {
    const { text: t, html } = render("/flies/forest");
    for (const label of ["Overview", "Charts", "Options flow", "Reports", "Advisor", "Live", "System", "Config", "MEIC", "Flies", "BWB", "Earnings", "GEX"]) {
      expect(t, `${label} missing from the palette`).toContain(label);
    }
    // The current module's tabs, first and searchable.
    expect(t).toContain("forest");
    expect(t).toContain("Flies tabs");
    // No module the suite turned off is offered (features undefined = everything on, but an off
    // module is the one rule worth proving the list obeys).
    expect(html).toContain('role="dialog"');
  });

  it("an off module is not offered — the same answer the rail renders", () => {
    const on = { configured: true, enabled: true, missing: [] };
    const features: SuiteFeaturesOk = {
      ok: true,
      capabilities: {},
      modules: { bwb: { ...on, enabled: false } },
      services: {},
      features: {},
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(["features"], features);
    vi.doMock("../src/lib/useFeatures", () => ({
      useFeatures: () => features,
    }));
    // Re-import with the mock in place.
    return import("../src/components/shell/CommandPalette?off").then(({ CommandPalette: OffPalette }) => {
      setPaletteOpen(true);
      const html = renderToString(
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={["/flies"]}>
            <OffPalette />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      setPaletteOpen(false);
      const t = text(html);
      expect(t).toContain("Flies");
      expect(t).not.toContain("BWB");
    });
  });

  it("a tab pick carries the page's query string — mode, date, arm ride along", () => {
    const { html } = render("/flies/forest?mode=live&session=2026-10-10");
    expect(html).toContain('href="/flies/session?mode=live&amp;session=2026-10-10"');
  });

  it("the shortcut hints are printed — the legend the header menu used to carry", () => {
    const { html } = render("/flies");
    // Suite pages are g-chords, modules bare digits.
    expect(html).toContain("g o");
    expect(html).toContain("g r");
    expect(html).toMatch(/>\d+</);
  });
});
