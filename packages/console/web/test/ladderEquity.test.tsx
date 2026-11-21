import { describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { LadderEquity, LadderEquityPoint } from "@console/shared";

vi.mock("../src/lib/prefs", () => ({
  useBoolPref: () => false,
  usePrefsSync: () => undefined,
  usePrefsVersion: () => 0,
  writePref: async () => undefined,
  getPref: () => undefined,
}));

const { LadderEquityCard, equityOrder, geometry, sharedDomain, ticks } = await import("../src/pages/Ladder/LadderEquityCard");

const pt = (session: string, realised: number, marked: number | null, over: Partial<LadderEquityPoint> = {}): LadderEquityPoint => ({
  session,
  realised,
  unrealised: marked === null ? null : Math.round((marked - realised) * 100) / 100,
  marked,
  openLots: 1,
  openCost: 900,
  unmarked: marked === null ? 1 : 0,
  ...over,
});

const html = (el: React.ReactElement) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!--\s*-->/g, "");

describe("ladder equity panels", () => {
  it("shares one scale across arms, with zero in it", () => {
    const d = sharedDomain({ a: [pt("2026-11-19", 50, 80)], b: [pt("2026-11-19", 300, -279)] });
    expect(d.lo).toBeLessThan(-279);
    expect(d.hi).toBeGreaterThan(300);
    expect(sharedDomain({ a: [pt("2026-11-19", 50, 80)] }).lo).toBeLessThan(0);
    expect(ticks({ lo: -300, hi: 300 })).toContain(0);
  });

  it("breaks the marked line at a session with no mark instead of bridging it", () => {
    const g = geometry([pt("2026-11-18", 0, 10), pt("2026-11-19", 0, null), pt("2026-11-20", 0, 20), pt("2026-11-23", 0, 25)], { lo: -50, hi: 50 });
    expect(g.marked.match(/M/g)).toHaveLength(2);
    expect(g.marked.match(/L/g)).toHaveLength(1);
    expect(g.over).toHaveLength(1); // only the 11-20 → 11-23 segment has both ends marked
  });

  it("shades held losses and held gains apart, split where the open lots cross zero", () => {
    const g = geometry([pt("2026-11-19", 100, 120), pt("2026-11-20", 100, 60)], { lo: 0, hi: 200 });
    expect(g.over).toHaveLength(1);
    expect(g.under).toHaveLength(1);
    const flat = geometry([pt("2026-11-19", 100, 100), pt("2026-11-20", 120, 120)], { lo: 0, hi: 200 });
    expect(flat.over.length + flat.under.length).toBe(0);
  });

  it("draws one panel per arm in the config's order, with both lines labelled at the end", () => {
    const eq: LadderEquity = {
      ok: true,
      error: null,
      arms: { session: [pt("2026-11-19", 0, -40), pt("2026-11-20", 347.54, 93.28)], chain: [pt("2026-11-20", 300.19, -279.14)], old: [] },
    };
    const order = equityOrder([{ arm: "chain" }, { arm: "session" }], eq);
    expect(order).toEqual(["chain", "session", "old"]);
    const out = html(<LadderEquityCard equity={eq} order={order} />);
    expect(out.indexOf('data-arm="chain"')).toBeLessThan(out.indexOf('data-arm="session"'));
    expect(out).not.toContain('data-arm="old"'); // an arm with no session has no panel
    expect(out).toContain("marked −$279");
    expect(out).toContain("realized $300");
    expect(out).toContain("open lots under water");
  });

  it("says when sessions have no mark, and when the read failed", () => {
    const gap: LadderEquity = { ok: true, error: null, arms: { chain: [pt("2026-11-19", 0, 10), pt("2026-11-20", 0, null)] } };
    expect(html(<LadderEquityCard equity={gap} order={["chain"]} />)).toContain("One session has no mark");
    const bad: LadderEquity = { ok: false, error: "ladder read unavailable — boom", arms: {} };
    expect(html(<LadderEquityCard equity={bad} order={[]} />)).toContain("ladder read unavailable — boom");
  });
});
