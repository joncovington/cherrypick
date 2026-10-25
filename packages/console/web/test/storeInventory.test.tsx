import { describe, expect, it } from "vitest";
import { fmtBytes, mainTable } from "../src/pages/Backtest/BacktestPage";

describe("the store inventory's lines", () => {
  it("reads a store by its largest table, counts only", () => {
    const s = {
      file: "eod_chains.db",
      tables: {
        eod_chain_requests: { rows: 182131, span: ["2014-02-07", "2026-10-22"] as [string, string] },
        eod_chain_quotes: { rows: 19542835, span: ["2016-01-05", "2026-10-22"] as [string, string] },
      },
    };
    expect(mainTable(s)).toBe("eod_chain_quotes: 19,542,835 rows · 2016-01-05 to 2026-10-22");
    expect(mainTable({ file: "x" })).toBe("—");
  });

  it("sizes in GB or MB", () => {
    expect(fmtBytes(2_650_000_000)).toBe("2.65 GB");
    expect(fmtBytes(31_000_000)).toBe("31 MB");
    expect(fmtBytes(undefined)).toBe("—");
  });
});
