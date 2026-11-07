import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { ExitReasonRow } from "@console/shared";
import { ExitReasonsBody, exitWinRate } from "../src/components/performance/PerformanceSlide";

const text = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

const ROWS: ExitReasonRow[] = [
  { tag: "control", reason: "profit_take", n: 8, wins: 8, net: 640, avgNet: 80 },
  { tag: "control", reason: "expired", n: 4, wins: 1, net: -900, avgNet: -225 },
];

describe("ExitReasonsBody", () => {
  it("shows each reason's win rate and average net beside its total", () => {
    const html = text(<ExitReasonsBody rows={ROWS} />);
    for (const head of ["win rate", "avg net", "net"]) expect(html).toContain(head);
    expect(html).toContain("100%");
    expect(html).toContain("25%"); // 1 of 4 expired trades won
    expect(html).toContain("-$225.00");
    expect(html).toContain("$80.00");
  });

  it("draws an empty row's win rate as a dash, never 0% or NaN", () => {
    const empty: ExitReasonRow = { tag: "control", reason: "voided", n: 0, wins: 0, net: null, avgNet: null };
    expect(exitWinRate(empty)).toBeNull();
    const html = text(<ExitReasonsBody rows={[empty]} />);
    expect(html).not.toContain("NaN");
    expect(html).not.toContain("0%");
  });
});
