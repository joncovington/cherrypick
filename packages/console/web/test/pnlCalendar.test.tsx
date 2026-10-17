import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { PnlCalendarResult, PnlDay, PnlExcursion, PnlTrade } from "@console/shared";
import {
  CalendarView,
  DayDetail,
  ExcursionView,
  MonthGrid,
  TradesTable,
  stamp,
  totalsBySession,
} from "../src/pages/Compare/CalendarTab";

const text = (node: React.ReactElement) => renderToString(<MemoryRouter>{node}</MemoryRouter>).replace(/<!--\s*-->/g, "");

const day = (module: PnlDay["module"], tag: string, session: string, net: number): PnlDay => ({ module, tag, session, net });

const rows: PnlDay[] = [
  day("flies", "control", "2026-10-13", -300),
  day("meic", "control", "2026-10-13", 500),
  day("flies", "control", "2026-10-14", 120),
  day("flies", "range-high", "2026-10-16", -80),
];

describe("totalsBySession", () => {
  it("sums every picked arm per session and keeps the arms", () => {
    const t = totalsBySession(rows);
    expect(t.get("2026-10-13")?.net).toBe(200);
    expect(t.get("2026-10-13")?.arms).toHaveLength(2);
    expect(t.has("2026-10-15")).toBe(false);
  });
});

describe("MonthGrid", () => {
  it("draws Mon–Fri weeks with a weekly total, and leaves a day nothing traded blank", () => {
    const html = text(<MonthGrid month="2026-10" totals={totalsBySession(rows)} picked={null} onPick={() => {}} />);
    // Week of 10-12: 200 + 120 - 80 = 240.
    expect(html).toContain("$240.00");
    expect(html).toContain("$200.00");
    expect(html).not.toMatch(/>\$0\.00</);
    expect(html).toContain(">Fri<");
    expect(html).not.toContain(">Sat<");
  });
});

describe("a week that straddles two months", () => {
  it("draws the neighbouring month's days so the row adds up to its week total", () => {
    const straddle = totalsBySession([day("flies", "control", "2026-09-30", 100), day("flies", "control", "2026-10-01", 50)]);
    const sept = text(<MonthGrid month="2026-09" totals={straddle} picked={null} onPick={() => {}} />);
    // The week of 09-28 shows October's $50 dimmed beside September's $100, and totals $150.
    expect(sept).toContain("$50.00");
    expect(sept).toContain("$150.00");
    // The month's own total counts only its own days.
    expect(sept).toMatch(/2026-09 <span class="pnl-pos">\$100\.00/);
  });
});

describe("DayDetail", () => {
  it("lists the arms that traded, largest move first", () => {
    const t = totalsBySession(rows).get("2026-10-13");
    const html = text(<DayDetail session="2026-10-13" total={t!} mode="paper" />);
    expect(html.indexOf("$500.00")).toBeLessThan(html.indexOf("-$300.00"));
  });
});

describe("CalendarView", () => {
  const data: PnlCalendarResult = { mode: "paper", from: "2026-08-21", note: null, days: rows, failures: [{ module: "curve", error: "x" }] };

  it("says which window it shows, and names a module that failed", () => {
    const html = text(<CalendarView data={data} visible={() => true} />);
    expect(html).toContain("the suite window, not each arm");
    expect(html).toContain("could not be read");
  });

  it("leaves out a module that is switched off", () => {
    const html = text(<CalendarView data={data} visible={(m) => m !== "meic" && m !== "curve"} />);
    // Without meic the 10-13 session is flies' -300 alone.
    expect(html).toContain("-$300.00");
    expect(html).not.toContain("could not be read");
  });
});

const trade = (over: Partial<PnlTrade>): PnlTrade => ({
  tag: "control",
  position_id: "SPX:control:2026-10-15",
  symbol: "SPX",
  strategy: "bwb_132",
  session: "2026-10-16",
  entry_time: "2026-10-15T10:00:30-04:00",
  exit_time: "2026-10-16T16:20:03-04:00",
  entry_spot: null,
  exit_spot: null,
  gross_pnl: 115,
  fees: 10.33,
  settlement: 0,
  slippage: 11.25,
  slippage_basis: "cost",
  net_pnl: 93.42,
  ...over,
});

describe("stamp", () => {
  it("shows the time, with the date in front when it is not the session's day", () => {
    expect(stamp("2026-10-16T16:20:03-04:00", "2026-10-16")).toBe("16:20");
    expect(stamp("2026-10-15T10:00:30-04:00", "2026-10-16")).toBe("10-15 10:00");
    expect(stamp("2026-10-16 15:29:37.556772-04:00", "2026-10-16")).toBe("15:29");
    expect(stamp(null, "2026-10-16")).toBe("—");
  });
});

describe("TradesTable", () => {
  it("lays money out gross, each cost, net, and names the slippage model", () => {
    const html = text(<TradesTable trades={[trade({})]} cellNet={93.42} />);
    for (const s of ["$115.00", "$10.33", "$11.25", "cost", "$93.42", "10-15 10:00", "16:20"]) expect(html).toContain(s);
    expect(html).not.toContain("does not reconcile");
  });

  it("reads an unrecorded cost as n/r, never $0.00", () => {
    const html = text(<TradesTable trades={[trade({ settlement: null, slippage: null })]} cellNet={93.42} />);
    expect(html).toContain("n/r");
    expect(html).not.toContain("$0.00");
  });

  it("says so when the trades do not sum to the cell", () => {
    const html = text(<TradesTable trades={[trade({})]} cellNet={100} />);
    expect(html).toContain("does not reconcile");
    expect(html).toContain("the cell says $100.00");
  });

  it("offers a trade's excursion only where it knows the module and the trade has a position id", () => {
    expect(text(<TradesTable trades={[trade({})]} cellNet={93.42} module="bwb" />)).toContain("▸");
    expect(text(<TradesTable trades={[trade({})]} cellNet={93.42} />)).not.toContain("▸");
    expect(text(<TradesTable trades={[trade({ position_id: null })]} cellNet={93.42} module="meic" />)).not.toContain("▸");
  });
});

const excursion = (over: Partial<PnlExcursion>): PnlExcursion => ({
  ok: true,
  recorded: true,
  basis: "gross_at_mid",
  ticks: 1200,
  refused_ticks: 4,
  best: { pnl: 90, at: "2026-08-28T10:20:11-04:00" },
  worst: { pnl: -135, at: "2026-08-24T12:02:55-04:00" },
  path: [
    ["2026-08-24T10:00:00-04:00", 0],
    ["2026-08-24T12:02:55-04:00", -135],
    ["2026-08-28T10:20:11-04:00", 90],
    ["2026-08-28T16:20:00-04:00", 85],
  ],
  endpoint: 85,
  gross: 85,
  reconciles: true,
  ...over,
});

describe("ExcursionView", () => {
  it("shows the best and worst with when, the exit, and the basis it is in", () => {
    const html = text(<ExcursionView x={excursion({})} />);
    for (const s of ["$90.00", "08-28 10:20", "-$135.00", "08-24 12:02", "exit $85.00", "equals the recorded gross"]) {
      expect(html).toContain(s);
    }
    expect(html).toContain("gross at mid");
    expect(html).toContain("4 refused");
  });

  it("flags an endpoint that does not reconcile with the recorded gross", () => {
    const html = text(<ExcursionView x={excursion({ reconciles: false, gross: 80 })} />);
    expect(html).toContain("does not reconcile with the recorded gross $80.00");
  });

  it("says why a module keeps no path, and draws nothing in its place", () => {
    const html = text(<ExcursionView x={{ ok: true, recorded: false, reason: "MEIC records each side's worst spot" }} />);
    expect(html).toContain("no excursion — MEIC records each side");
    expect(html).not.toContain("<svg");
  });
});
