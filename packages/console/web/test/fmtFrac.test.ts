import { describe, it, expect } from "vitest";
import { fmtFrac, fmtGrouped } from "../src/lib/format";

describe("fmtFrac", () => {
  it("shows a 0-1 fraction as a percentage at the digits asked", () => {
    expect(fmtFrac(0.452, 1)).toBe("45.2%");
    expect(fmtFrac(0.452)).toBe("45%");
    expect(fmtFrac(0.05, 2)).toBe("5.00%");
    expect(fmtFrac(-0.0125, 1)).toBe("-1.3%");
  });

  it("is a dash for a missing value, never a zero", () => {
    expect(fmtFrac(null)).toBe("—");
    expect(fmtFrac(undefined, 1)).toBe("—");
    expect(fmtFrac(0, 1)).toBe("0.0%");
  });
});

describe("fmtGrouped", () => {
  it("groups thousands and pins the decimals, in en-US whatever the viewer's locale", () => {
    expect(fmtGrouped(1234.5)).toBe("1,234.50");
    expect(fmtGrouped(0)).toBe("0.00");
    expect(fmtGrouped(-98765.4321)).toBe("-98,765.43");
    expect(fmtGrouped(7, 0)).toBe("7");
  });

  it("is a dash for a missing value", () => {
    expect(fmtGrouped(null)).toBe("—");
    expect(fmtGrouped(undefined)).toBe("—");
  });
});
