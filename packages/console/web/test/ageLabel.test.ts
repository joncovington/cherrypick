import { describe, it, expect } from "vitest";
import { ageLabel } from "../src/lib/format";

describe("ageLabel", () => {
  it("names only the units an age needs, in hours, minutes and seconds", () => {
    expect(ageLabel(null)).toBe("—");
    expect(ageLabel(0)).toBe("0s");
    expect(ageLabel(45.4)).toBe("45s");
    expect(ageLabel(192)).toBe("3m 12s");
    expect(ageLabel(3600)).toBe("1h 0m 0s");
    expect(ageLabel(43_867)).toBe("12h 11m 7s");
  });

  it("compact drops the seconds once an age reaches an hour, and only then", () => {
    expect(ageLabel(43_867, true)).toBe("12h 11m");
    expect(ageLabel(192, true)).toBe("3m 12s");
  });
});
