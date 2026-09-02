import { describe, it, expect, afterEach } from "vitest";
import { formatAsOf, asOfIsToday } from "../src/pages/Gex/GexPage";

/**
 * A GEX profile's "as of" chip carried a bare time, which reads as "now".
 *
 * The shared stream cache retains chains for underlyings the suite retired weeks ago, and until
 * 2026-09-02 the profile route would build one from a dead chain -- rendering "as of 20:14 ET" for
 * a five-week-old reading, indistinguishable from tonight. The route now refuses those symbols;
 * this is the second half, so that any stale reading that does reach the chip announces itself.
 *
 * Checked from several timezones, the same property `etTime` protects: the answer must not depend
 * on where the console is being read from, or the comparison is only right in New York.
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

describe("the GEX as-of chip", () => {
  it("dates a reading that is not from today, so it cannot read as now", () => {
    everywhere(() => {
      const weeksAgo = Date.now() / 1000 - 35 * 24 * 3600;
      expect(asOfIsToday(weeksAgo)).toBe(false);
      // A date component is present -- the exact format is the locale's business, the presence is not.
      expect(formatAsOf(weeksAgo)).toMatch(/\d{1,2}\/\d{1,2}\/\d{4}/);
    });
  });

  it("leaves a reading from today as a bare time, because the date would be noise", () => {
    everywhere(() => {
      const now = Date.now() / 1000;
      expect(asOfIsToday(now)).toBe(true);
      expect(formatAsOf(now)).not.toMatch(/\d{1,2}\/\d{1,2}\/\d{4}/);
      expect(formatAsOf(now)).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    });
  });
});
