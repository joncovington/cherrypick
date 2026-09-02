import { describe, it, expect, afterEach } from "vitest";
import { parseSuiteTs, etMinuteOfDay, etClock } from "../src/lib/etTime";

/**
 * Run a check from several places on Earth.
 *
 * Node re-reads `process.env.TZ` between Date operations, which lets these assert the property that
 * actually matters: the answer does not depend on where the console is being read from. Without
 * this the whole file would pass in New York against the very implementation that broke MEIC —
 * offset-naive parsing is only correct when the viewer already sits in ET.
 */
const ZONES = ["America/New_York", "America/Denver", "UTC", "Asia/Tokyo"];
const REAL_TZ = process.env.TZ;

function everywhere(check: () => void): void {
  for (const tz of ZONES) {
    process.env.TZ = tz;
    try {
      check();
    } catch (err) {
      throw new Error(`failed with TZ=${tz}: ${(err as Error).message}`);
    }
  }
}

afterEach(() => {
  if (REAL_TZ === undefined) delete process.env.TZ;
  else process.env.TZ = REAL_TZ;
});

/**
 * The suite records on the ET session clock; the console is read from wherever the developer is.
 * Both of these have already shipped broken once, in opposite directions, and neither was visible
 * as an error — just a chart quietly disagreeing with the numbers beside it. So the assertions here
 * are absolute (a known instant, a known ET wall clock) rather than relative to the machine running
 * them: a test that passes only in New York would have caught neither bug.
 */

// 09:30:00 ET on 2026-08-13 (EDT, UTC-4), stated unambiguously.
const OPEN_ET = Date.parse("2026-08-13T09:30:00-04:00");
const SESSION_OPEN_MIN = 9 * 60 + 30;

describe("the two ledger formats mean the same instant", () => {
  it("flies' offset-bearing stamp parses as written", () => {
    everywhere(() => expect(parseSuiteTs("2026-08-13T09:30:00-04:00")).toBe(OPEN_ET));
  });

  it("MEIC's offset-naive stamp is ET, not the viewer's local clock", () => {
    // The whole bug: Date.parse would read this as local time.
    everywhere(() => expect(parseSuiteTs("2026-08-13T09:30:00")).toBe(OPEN_ET));
  });

  it("a space separator is accepted too", () => {
    everywhere(() => expect(parseSuiteTs("2026-08-13 09:30:00")).toBe(OPEN_ET));
  });

  it("seconds are optional", () => {
    everywhere(() => expect(parseSuiteTs("2026-08-13T09:30")).toBe(OPEN_ET));
  });

  it("a UTC stamp is honoured as UTC", () => {
    everywhere(() => expect(parseSuiteTs("2026-08-13T13:30:00Z")).toBe(OPEN_ET));
  });

  it("winter stamps use EST, not a hardcoded summer offset", () => {
    const winterOpen = Date.parse("2026-01-15T09:30:00-05:00");
    everywhere(() => expect(parseSuiteTs("2026-01-15T09:30:00")).toBe(winterOpen));
  });
});

describe("minute of day is measured on the ET session clock", () => {
  it("both formats put the open at 570", () => {
    everywhere(() => {
      expect(etMinuteOfDay("2026-08-13T09:30:00-04:00")).toBe(SESSION_OPEN_MIN);
      expect(etMinuteOfDay("2026-08-13T09:30:00")).toBe(SESSION_OPEN_MIN);
    });
  });

  it("carries seconds as a fraction, so marks inside a minute stay ordered", () => {
    expect(etMinuteOfDay("2026-08-13T09:30:30-04:00")).toBeCloseTo(SESSION_OPEN_MIN + 0.5, 6);
  });

  it("puts the close at 960", () => {
    everywhere(() => {
      expect(etMinuteOfDay("2026-08-13T16:00:00-04:00")).toBe(16 * 60);
      expect(etMinuteOfDay("2026-08-13T16:00:00")).toBe(16 * 60);
    });
  });

  it("keeps a whole session inside the axis for both modules", () => {
    // flies stamps with an offset, MEIC without — the same session must land in the same place.
    everywhere(() => {
      for (const ts of ["2026-08-13T09:30:15-04:00", "2026-08-13T09:30:15"]) {
        const min = etMinuteOfDay(ts);
        expect(min).not.toBeNull();
        expect(min!).toBeGreaterThanOrEqual(SESSION_OPEN_MIN);
        expect(min!).toBeLessThanOrEqual(16 * 60);
      }
    });
  });
});

describe("printed times", () => {
  it("read in ET for both formats", () => {
    everywhere(() => {
      expect(etClock("2026-08-13T09:30:00-04:00")).toBe("09:30");
      expect(etClock("2026-08-13T09:30:00")).toBe("09:30");
    });
  });
});

describe("unusable input", () => {
  it("is null rather than a wrong number", () => {
    for (const bad of [null, "", "   ", "not a time", "09:30"]) {
      expect(parseSuiteTs(bad)).toBeNull();
      expect(etMinuteOfDay(bad)).toBeNull();
    }
    expect(etClock(null)).toBe("—");
  });
});
