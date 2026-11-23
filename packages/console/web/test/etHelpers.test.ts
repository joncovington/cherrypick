import { afterEach, describe, expect, it } from "vitest";
import { etTimeOfDay, etToday, hhmmOf } from "../src/lib/etTime";

/** The shared ET date, freshness stamp and clock slice. Absolute instants, read from several zones. */

const REAL_TZ = process.env.TZ;
afterEach(() => {
  if (REAL_TZ === undefined) delete process.env.TZ;
  else process.env.TZ = REAL_TZ;
});

describe("etToday and etTimeOfDay", () => {
  it("name the New York date and clock whatever zone the viewer is in", () => {
    // 2026-10-21 03:30 UTC is 23:30 on the 20th in New York (EDT).
    const late = new Date(Date.UTC(2026, 9, 21, 3, 30, 0));
    // 2026-10-20 18:32:05 UTC is 14:32:05 in New York.
    const afternoon = Date.UTC(2026, 9, 20, 18, 32, 5);
    for (const tz of ["America/New_York", "America/Denver", "UTC", "Asia/Tokyo"]) {
      process.env.TZ = tz;
      expect(etToday(late), tz).toBe("2026-10-20");
      expect(etTimeOfDay(afternoon), tz).toBe("14:32:05");
    }
  });
});

describe("hhmmOf", () => {
  it("takes the clock of a stamp as written, and is a dash for none", () => {
    expect(hhmmOf("2026-10-20T14:32:05")).toBe("14:32");
    expect(hhmmOf("2026-10-20 09:30")).toBe("09:30");
    expect(hhmmOf(null)).toBe("—");
    expect(hhmmOf("2026-10-20")).toBe("—");
  });
});
