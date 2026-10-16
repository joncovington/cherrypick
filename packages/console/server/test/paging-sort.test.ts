import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";
import { pageArray, pagedQuery, parsePage, sortBy } from "../src/readers/paging.js";

/**
 * Server-side sort for the paged tables (2026-10-16). The property that matters is that a sorted
 * page is a slice of the WHOLE sorted result: page 1 of "net, largest first" holds the largest
 * nets in the history, not the largest of whichever 100 rows the default order happened to fetch.
 */
interface Row {
  id: number;
  gross: number;
  fees: number;
  net: number;
}

function db(): Database.Database {
  const d = new Database(":memory:");
  d.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, gross REAL, fees REAL)");
  const ins = d.prepare("INSERT INTO t (id, gross, fees) VALUES (?, ?, ?)");
  // Newest (highest id) first by default; the largest net sits on the LAST default page.
  for (let id = 1; id <= 10; id++) ins.run(id, id === 1 ? 500 : id * 10, 1);
  return d;
}

const spec = { columns: "id, gross, fees", from: "t", where: "1=1", params: [], orderBy: "id DESC" };
// The net is derived in the reader, not stored: the sort must use the derived figure.
const map = (r: Record<string, unknown>): Row => ({
  id: Number(r["id"]),
  gross: Number(r["gross"]),
  fees: Number(r["fees"]),
  net: Number(r["gross"]) - Number(r["fees"]),
});
const sorts = { net: (r: Row) => r.net };

describe("pagedQuery with a sort", () => {
  it("pages the whole sorted set, so the first page holds the history's largest net", () => {
    const page = pagedQuery(db(), spec, { limit: 3, offset: 0, sort: "net", dir: "desc" }, map, sorts);
    expect(page.rows.map((r) => r.id)).toEqual([1, 10, 9]);
    expect(page.total).toBe(10);
    // Without the sort, the same window is the reader's own order.
    expect(pagedQuery(db(), spec, { limit: 3, offset: 0 }, map, sorts).rows.map((r) => r.id)).toEqual([10, 9, 8]);
  });

  it("ascending, and the second page continues where the first stopped", () => {
    const p2 = pagedQuery(db(), spec, { limit: 3, offset: 3, sort: "net", dir: "asc" }, map, sorts);
    expect(p2.rows.map((r) => r.id)).toEqual([5, 6, 7]);
  });

  it("ignores a sort the reader does not name, rather than ordering by something unrelated", () => {
    const page = pagedQuery(db(), spec, { limit: 3, offset: 0, sort: "gross; DROP TABLE t", dir: "asc" }, map, sorts);
    expect(page.rows.map((r) => r.id)).toEqual([10, 9, 8]);
  });
});

describe("sortBy", () => {
  it("keeps nulls last in both directions and ties in their original order", () => {
    const rows = [
      { k: "a", v: 2 as number | null },
      { k: "b", v: null },
      { k: "c", v: 2 },
      { k: "d", v: 5 },
    ];
    expect(sortBy(rows, (r) => r.v, "desc").map((r) => r.k)).toEqual(["d", "a", "c", "b"]);
    expect(sortBy(rows, (r) => r.v, "asc").map((r) => r.k)).toEqual(["a", "c", "d", "b"]);
  });
});

describe("pageArray and parsePage", () => {
  it("sorts the materialised list before slicing it", () => {
    const rows = [3, 9, 1, 7].map((n) => ({ n }));
    const page = pageArray(rows, { limit: 2, offset: 0, sort: "n", dir: "desc" }, { n: (r) => r.n });
    expect(page.rows.map((r) => r.n)).toEqual([9, 7]);
    expect(page.total).toBe(4);
  });

  it("reads sort and dir under the table's prefix, defaulting the direction to desc", () => {
    expect(parsePage({ tradesSort: "net", tradesLimit: "50" }, "trades")).toEqual({ limit: 50, offset: 0, sort: "net", dir: "desc" });
    expect(parsePage({ sort: "net", dir: "asc" })).toMatchObject({ sort: "net", dir: "asc" });
    expect(parsePage({})).toEqual({ limit: 100, offset: 0 });
  });
});
