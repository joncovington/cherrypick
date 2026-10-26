import { describe, expect, it } from "vitest";
import { fmtBytes, mainTable } from "../src/pages/Backtest/BacktestPage";

describe("the store inventory's lines", () => {
  it("reads a store by its largest table, counts only", () => {
    const s = {
      file: "eod_chains.db",
      tables: {
        eod_chain_requests: { rows: 12345, span: ["2020-01-02", "2024-12-31"] as [string, string] },
        eod_chain_quotes: { rows: 1234567, span: ["2020-01-02", "2024-12-31"] as [string, string] },
      },
    };
    expect(mainTable(s)).toBe("eod_chain_quotes: 1,234,567 rows · 2020-01-02 to 2024-12-31");
    expect(mainTable({ file: "x" })).toBe("—");
  });

  it("sizes in GB or MB", () => {
    expect(fmtBytes(2_650_000_000)).toBe("2.65 GB");
    expect(fmtBytes(31_000_000)).toBe("31 MB");
    expect(fmtBytes(undefined)).toBe("—");
  });
});
