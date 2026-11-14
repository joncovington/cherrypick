import { describe, expect, it } from "vitest";
import type { MorningCalendar, MorningGate, MorningSectorRow } from "@console/shared";
import { dayLabel, gateMarks, morningViews, sectorCounts, sparkPoints, upcomingDays, viewFrom } from "../src/pages/Morning/brief";
import { ordinal } from "../src/pages/Morning/MorningBrief";

const gate = (id: string, status: string): MorningGate => ({ id, label: id, status, value: null, threshold: null, detail: null });

describe("the morning report's tabs", () => {
  it("Charts and Note leave the strip with their features", () => {
    expect(morningViews({ technicals: true, narrative: true }).map((v) => v.id)).toEqual(["brief", "risk", "tape", "charts", "options", "calendar", "note"]);
    expect(morningViews({ technicals: false, narrative: false }).map((v) => v.id)).toEqual(["brief", "risk", "tape", "options", "calendar"]);
  });

  it("a view not on show reads as the Brief", () => {
    const views = morningViews({ technicals: false, narrative: true });
    expect(viewFrom("risk", views)).toBe("risk");
    expect(viewFrom(null, views)).toBe("brief");
    expect(viewFrom("charts", views)).toBe("brief");
    expect(viewFrom("bogus", views)).toBe("brief");
  });
});

describe("gate marks", () => {
  it("only met is a tick; anything unfamiliar is unknown, never met", () => {
    expect(gateMarks([gate("a", "met"), gate("b", "not_met"), gate("c", "unknown"), gate("d", "weird")]).map((g) => g.mark)).toEqual(["✓", "✗", "?", "?"]);
  });
});

const cal = (over: Partial<MorningCalendar>): MorningCalendar => ({
  isFomcDay: false,
  nextFomc: null,
  fomcYearKnown: null,
  isTripleWitching: false,
  isQuarterlyExpiry: false,
  nextTradingDay: null,
  releases: null,
  earnings: null,
  ...over,
});

const earn = (symbol: string, date: string, movePct: number | null) => ({
  symbol,
  date,
  when: null,
  expiration: null,
  spot: null,
  strike: null,
  straddle: null,
  expectedMove: null,
  expectedMovePct: movePct,
  reason: null,
});

describe("the Brief's calendar", () => {
  it("groups releases and earnings by date from the session on, the first three dates only", () => {
    const days = upcomingDays(
      cal({
        releases: [
          { name: "old", date: "2026-11-12", timeEt: null, source: null, today: false },
          { name: "PPI", date: "2026-11-13", timeEt: null, source: null, today: true },
          { name: "retail", date: "2026-11-17", timeEt: null, source: null, today: false },
          { name: "later", date: "2026-11-20", timeEt: null, source: null, today: false },
        ],
        earnings: { rows: [earn("XP", "2026-11-16", 7.1), earn("BIDU", "2026-11-16", 6.8)], asOf: null, reason: null },
      }),
      "2026-11-13",
    );
    expect(days.map((d) => d.date)).toEqual(["2026-11-13", "2026-11-16", "2026-11-17"]);
    expect(days[0]).toMatchObject({ today: true, releases: ["PPI"] });
    expect(days[1]!.earnings.map((e) => e.symbol)).toEqual(["XP", "BIDU"]);
  });

  it("orders a day's earnings by implied move, an unpriced one last", () => {
    const [d] = upcomingDays(
      cal({ earnings: { rows: [earn("A", "2026-11-16", null), earn("B", "2026-11-16", 3), earn("C", "2026-11-16", 9)], asOf: null, reason: null } }),
      "2026-11-13",
    );
    expect(d!.earnings.map((e) => e.symbol)).toEqual(["C", "B", "A"]);
  });

  it("no calendar block is no days, not an error", () => {
    expect(upcomingDays(null, "2026-11-13")).toEqual([]);
  });

  it("names a day by its weekday and date, and the session as today", () => {
    expect(dayLabel("2026-11-16", false)).toBe("Mon 16");
    expect(dayLabel("2026-11-13", true)).toBe("today");
  });
});

describe("the Brief's small figures", () => {
  it("counts a flat sector as neither up nor down, and an unmeasured one apart", () => {
    const row = (changePct: number | null): MorningSectorRow => ({ symbol: "X", sector: null, changePct, close: null, session: null });
    expect(sectorCounts([row(1), row(-1), row(0), row(null), row(2)])).toEqual({ up: 2, down: 1, unmeasured: 1 });
  });

  it("draws the curve only from measured points, and nothing from fewer than two", () => {
    const p = (symbol: string, value: number | null) => ({ point: symbol, symbol, dte: 1, value, basis: null });
    expect(sparkPoints([p("VIX", 18), p("VIX3M", null)], 120, 34)).toEqual([]);
    const pts = sparkPoints([p("VIX9D", 17), p("VIX", 18), p("VIX3M", 20)], 120, 34);
    expect(pts.map((q) => q.symbol)).toEqual(["VIX9D", "VIX", "VIX3M"]);
    expect(pts[2]!.y).toBeLessThan(pts[0]!.y);
  });

  it("writes ordinals the English way", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 53, 63, 78, 111].map(ordinal)).toEqual(["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "53rd", "63rd", "78th", "111th"]);
  });
});
