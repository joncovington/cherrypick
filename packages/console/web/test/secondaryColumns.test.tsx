import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageTrade } from "@console/shared";
import { SecondaryCells, columnsFor, secondaryOf, type Column } from "../src/components/columns";
import { earningsInLife } from "../src/pages/Live/ManagePage";

/**
 * Batch 3 of docs/console-review-plan.md: a wide table keeps a short row in every view and moves the
 * rest into the row's expanded line. A secondary column must be in exactly one of the two places.
 */

type R = { a: number; b: number; c: number };
const COLS: Column<R>[] = [
  { key: "a", label: "A", td: (r) => <td>{r.a}</td> },
  { key: "b", label: "B", detailOnly: true, td: (r) => <td>{r.b}</td> },
  { key: "c", label: "C", secondary: true, td: (r) => <td>{r.c}</td> },
];

describe("secondary columns", () => {
  it("leave the row in both views, and only they go to the expanded line", () => {
    expect(columnsFor(COLS, "detail").map((c) => c.key)).toEqual(["a", "b"]);
    expect(columnsFor(COLS, "simple").map((c) => c.key)).toEqual(["a"]);
    expect(secondaryOf(COLS).map((c) => c.key)).toEqual(["c"]);
  });

  it("render under their own headers in the expanded line, and nothing when there are none", () => {
    const html = renderToString(<SecondaryCells cols={secondaryOf(COLS)} row={{ a: 1, b: 2, c: 3 }} />);
    expect(html).toContain("<th>C</th>");
    expect(html).toContain("<td>3</td>");
    expect(renderToString(<SecondaryCells cols={[]} row={{ a: 1, b: 2, c: 3 }} />)).toBe("");
  });
});

describe("Live positions' earnings column", () => {
  const t = (days: number | null, dte: number | null) =>
    ({ earnings: days === null ? null : { date: "2026-11-05", days, timeOfDay: null, beforeNearestShortExpiry: null }, nearestShortDte: dte }) as unknown as ManageTrade;

  it("stays in the row only when an announcement falls inside a position's life", () => {
    expect(earningsInLife(t(3, 10))).toBe(true);
    expect(earningsInLife(t(12, 10))).toBe(false);
    expect(earningsInLife(t(null, 10))).toBe(false);
    expect(earningsInLife(t(3, null))).toBe(true);
  });
});
