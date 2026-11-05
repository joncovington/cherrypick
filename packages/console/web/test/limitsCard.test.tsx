import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageLimitRow, ManageLimits } from "@console/shared";
import { LimitsCard, barFill } from "../src/pages/Live/LimitsCard";

/**
 * Live → portfolio's "your limits" draws manage's `limits` and decides nothing: the state and whose
 * limit it is are manage's, the bar is the value's share of the way to the edge it faces, and an
 * unknown day's P&L says why rather than showing zero. Synthetic figures.
 */

function text(html: string): string {
  return html
    .replace(/<!-- -->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function row(over: Partial<ManageLimitRow> = {}): ManageLimitRow {
  return {
    id: "delta",
    label: "delta, beta-weighted",
    unit: "shares",
    value: -12,
    low: -8,
    high: 10,
    source: "guidance band",
    state: "over",
    ...over,
  };
}

const LIMITS: ManageLimits = {
  rows: [
    row(),
    row({ id: "buying_power", label: "buying power used / net liq", unit: "share", value: 0.27, low: null, high: 0.3, state: "near" }),
    row({ id: "vega", label: "vega, per vol point", unit: "$", value: -40, low: null, high: null, source: "none", state: "no limit" }),
  ],
  unknownNames: ["CCC"],
  dayLoss: { pnl: null, alert: -200, crossed: null, why: "AAA 1 has no mark" },
};

describe("your limits card", () => {
  it("shows each measure, whose limit it is and manage's state", () => {
    const t = text(renderToString(<LimitsCard limits={LIMITS} />));
    expect(t).toContain("delta, beta-weighted · guidance band");
    expect(t).toContain("-12.0 / -8.0 to 10.0");
    expect(t).toContain("27.0% / up to 30.0%");
    expect(t).toContain("no limit");
    expect(t).toContain("the day's P&L is unknown: AAA 1 has no mark");
    expect(t).toContain("no worst case modelled for CCC");
  });

  it("fills the bar toward the edge the value faces", () => {
    expect(barFill(row())).toBe(100); // past -8: capped
    expect(barFill(row({ value: -4 }))).toBe(50);
    expect(barFill(row({ value: 5 }))).toBe(50);
    expect(barFill(row({ value: 0.27, low: null, high: 0.3 }))).toBeCloseTo(90);
    expect(barFill(row({ value: -40, low: null, high: null }))).toBe(0);
    expect(barFill(row({ value: null }))).toBe(0);
  });
});
