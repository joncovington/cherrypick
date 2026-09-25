import { describe, expect, it } from "vitest";
import { defaultLayout, resolveColumns, type ColumnDef } from "../src/components/table/columns";
import { presetRange } from "../src/components/table/DateRange";

/**
 * The history tables' column rules (components/table/columns.ts). Describe columns go anywhere and
 * can be hidden; the money block keeps the standard's order at the end; net is always shown and
 * always last -- a history without its net, or with it mid-row, no longer adds up left to right.
 */

type Row = Record<string, never>;
const col = (id: string, kind: "describe" | "money", pinned = false): ColumnDef<Row> => ({
  id,
  header: id,
  kind,
  pinned,
  render: () => null,
});
const DEFS = [
  col("date", "describe"),
  col("sym", "describe"),
  col("arm", "describe"),
  col("entry", "money"),
  col("gross", "money"),
  col("fees", "money"),
  col("net", "money", true),
];
const ids = (layout: { order: string[]; hidden: string[] }) => resolveColumns(DEFS, layout).map((c) => c.id);

describe("resolveColumns", () => {
  it("renders the declaration by default", () => {
    expect(ids(defaultLayout(DEFS))).toEqual(["date", "sym", "arm", "entry", "gross", "fees", "net"]);
  });

  it("reorders describe columns and keeps the money block after them in the standard's order", () => {
    expect(ids({ order: ["arm", "date", "sym"], hidden: [] })).toEqual(["arm", "date", "sym", "entry", "gross", "fees", "net"]);
  });

  it("never lets a money column into the describe order", () => {
    expect(ids({ order: ["net", "gross", "date", "sym", "arm"], hidden: [] })).toEqual([
      "date", "sym", "arm", "entry", "gross", "fees", "net",
    ]);
  });

  it("hides any column but net", () => {
    expect(ids({ order: ["date", "sym", "arm"], hidden: ["sym", "fees", "net"] })).toEqual(["date", "arm", "entry", "gross", "net"]);
  });

  it("drops an id the table no longer has, and shows a column declared since the layout was saved", () => {
    // Saved before `sym` existed, and naming a column since removed.
    expect(ids({ order: ["arm", "gone", "date"], hidden: [] })).toEqual(["arm", "date", "sym", "entry", "gross", "fees", "net"]);
  });
});

describe("presetRange", () => {
  it("runs this week from Monday", () => {
    expect(presetRange("week", "2026-09-25")).toEqual(["2026-09-21", "2026-09-25"]); // a Friday
    expect(presetRange("week", "2026-09-21")).toEqual(["2026-09-21", "2026-09-21"]); // a Monday
    expect(presetRange("week", "2026-09-27")).toEqual(["2026-09-21", "2026-09-27"]); // a Sunday
  });

  it("runs this month from the first, and 30 days inclusively", () => {
    expect(presetRange("month", "2026-09-25")).toEqual(["2026-09-01", "2026-09-25"]);
    expect(presetRange("30d", "2026-09-25")).toEqual(["2026-08-27", "2026-09-25"]);
  });
});
