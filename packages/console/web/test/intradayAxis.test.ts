import { describe, expect, it } from "vitest";
import { TickMarkType, type Time } from "lightweight-charts";
import { etTickMark, visibleSpanLabel } from "../src/pages/Intraday/IntradayPage";

/**
 * The live chart's time axis reads on the ET session clock. lightweight-charts marks a new day on the
 * first bar of each UTC day — a cash symbol's 09:30 open, or 20:00 ET (summer) on a future. Dating that
 * tick with the UTC day put "Oct 1" at 20:00 on Oct 1 (the first browser check, 2026-10-01); labelling
 * it with the time alone left a week of cash sessions as a row of bold "09:30"s with no day on any of
 * them (2026-11-14). It carries its own ET weekday and date beside its ET time.
 */

const at = (iso: string): Time => (Date.parse(iso) / 1000) as Time;

describe("the intraday time axis", () => {
  it("a day-change tick on a cash open carries its ET weekday and date", () => {
    expect(etTickMark(at("2026-11-13T14:30:00Z"), TickMarkType.DayOfMonth)).toBe("Fri 13 09:30");
    expect(etTickMark(at("2026-11-16T14:30:00Z"), TickMarkType.Month)).toBe("Mon 16 09:30");
  });

  it("a day-change tick at UTC midnight carries the ET date, not the UTC one", () => {
    expect(etTickMark(at("2026-10-02T00:00:00Z"), TickMarkType.DayOfMonth)).toBe("Thu 1 20:00");
    expect(etTickMark(at("2026-12-02T00:00:00Z"), TickMarkType.DayOfMonth)).toBe("Tue 1 19:00"); // EST
  });

  it("ET midnight is the date alone", () => {
    expect(etTickMark(at("2026-10-02T04:00:00Z"), TickMarkType.Time)).toBe("Fri Oct 2");
  });

  it("the status line names the days in view, on the ET clock", () => {
    const s = (iso: string): number => Date.parse(iso) / 1000;
    // /NQ at 5m: Thursday evening's open to Friday's close
    expect(visibleSpanLabel(s("2026-11-12T23:00:00Z"), s("2026-11-13T21:55:00Z"))).toBe("Thu Nov 12 18:00 – Fri Nov 13 16:55 ET");
    // one cash session
    expect(visibleSpanLabel(s("2026-11-13T14:30:00Z"), s("2026-11-13T20:55:00Z"))).toBe("Fri Nov 13 09:30–15:55 ET");
  });

  it("an ordinary tick is the ET time", () => {
    expect(etTickMark(at("2026-10-02T03:50:00Z"), TickMarkType.Time)).toBe("23:50");
    expect(etTickMark(at("2026-10-02T00:00:00Z"))).toBe("20:00");
  });
});
