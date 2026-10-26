import { describe, expect, it } from "vitest";
import type { CreditSpreadRow } from "@console/shared";
import { DEFAULT_SELECTION, rowsFor } from "../src/pages/Screeners/CreditSpreadTab";

const price = (long: number) => ({ long, width: 5, credit: 1, creditMid: 1.05, creditWidth: 0.2, maxLoss: 4, creditMaxLoss: 0.25, passes: true });
const short = (strike: number, delta: number) => ({ strike, delta, iv: 0.3, bid: 1, ask: 1.1 });

function row(symbol: string, sel: Record<string, unknown> | undefined): CreditSpreadRow {
  return { symbol, spread: "put", short: short(95, -0.4), pick: price(90), selections: sel } as unknown as CreditSpreadRow;
}

describe("the credit-spread selections", () => {
  const rows = [
    row("REV", { "reversion-d20-s5": { short: short(88, -0.2), price: price(83) } }),
    row("PULL", { "reversion-d20-s5": null }),
    row("OLD", undefined),
  ];

  it("keeps every row under the credit rule", () => {
    expect(rowsFor(rows, DEFAULT_SELECTION).map((r) => r.symbol)).toEqual(["REV", "PULL", "OLD"]);
  });

  it("keeps only a selection's rows, carrying its own short and spread", () => {
    const out = rowsFor(rows, "reversion-d20-s5");
    expect(out.map((r) => r.symbol)).toEqual(["REV"]);
    expect(out[0]!.short.strike).toBe(88);
    expect(out[0]!.pick?.long).toBe(83);
  });
});
