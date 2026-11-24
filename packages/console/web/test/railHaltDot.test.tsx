import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HaltDot, ModuleNav } from "../src/components/shell/ModuleNav";
import { liveScopeDefaults } from "../src/lightbox/manifests/MeicLightbox";

function rail(halted: boolean | null): string {
  const qc = new QueryClient();
  if (halted !== null) qc.setQueryData(["config-lock"], { halted });
  return renderToString(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/overview"]}>
        <ModuleNav module={null} slide="" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("the halted dot on the rail", () => {
  it("draws nothing when clear", () => {
    expect(HaltDot({ halted: false })).toBeNull();
    expect(rail(false)).not.toContain("nav-halt-dot");
    expect(rail(null)).not.toContain("nav-halt-dot");
  });

  it("is named, and sits on the Live link in the list and on the collapsed strip", () => {
    const html = rail(true);
    expect(html.match(/nav-halt-dot/g)?.length).toBe(2);
    expect(html).toContain('aria-label="live entries halted"');
    expect(html).toContain('title="live entries halted"');
    // both dots are inside a link to /live
    for (const m of html.matchAll(/<a [^>]*href="\/live"[^>]*>(.*?)<\/a>/g)) expect(m[1]).toContain("nav-halt-dot");
  });
});

describe("live MEIC scope defaults", () => {
  const base = { arms: ["eq-hourly", "pm-200"], symbols: ["SPX", "XSP"], liveArm: "pm-200", liveSymbol: "SPX" };
  const stats = {
    armStats: [{ arm: "eq-hourly", trades: 9, sessions: 3 }],
    symbolStats: [{ symbol: "XSP", trades: 9, sessions: 3 }],
  };
  it("opens on the configured live arm and symbol", () => {
    expect(liveScopeDefaults({ ...base, ...stats })).toEqual({ arm: "pm-200", symbol: "SPX" });
  });
  it("falls to the busiest when the configured one has no trades, never to all", () => {
    expect(liveScopeDefaults({ ...base, ...stats, liveArm: "gone", liveSymbol: null })).toEqual({ arm: "eq-hourly", symbol: "XSP" });
    expect(liveScopeDefaults(undefined)).toEqual({ arm: null, symbol: null });
  });
});
