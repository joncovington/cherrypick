import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ColumnDef } from "../src/components/table/columns";

/**
 * Which headers of a history table sort (2026-10-16). The rule worth pinning is the paged one: a
 * server-paged table only offers the columns its reader can order the whole result by, so a column
 * with a `sortValue` still renders as plain text there -- sorting one fetched page in the browser
 * would rank 100 rows and call it the history.
 */

vi.mock("../src/lib/prefs", () => ({
  useBoolPref: () => false,
  usePrefsSync: () => undefined,
  usePrefsVersion: () => 0,
  writePref: async () => undefined,
  getPref: () => undefined,
}));

const { HistoryTable } = await import("../src/components/table/HistoryTable");
const { nextSort } = await import("../src/components/ScopeBar");

interface Row {
  id: string;
  net: number;
}
const DEFS: ColumnDef<Row>[] = [
  { id: "id", header: "trade", kind: "describe", sortValue: (r) => r.id, render: (r) => r.id },
  { id: "note", header: "note", kind: "describe", render: () => "x" },
  { id: "net", header: "net", kind: "money", pinned: true, sortValue: (r) => r.net, render: (r) => String(r.net) },
];
const ROWS: Row[] = [
  { id: "a", net: 5 },
  { id: "b", net: -3 },
];

function render(props: Partial<Parameters<typeof HistoryTable<Row>>[0]>): string {
  return renderToString(
    <MemoryRouter>
      <HistoryTable<Row> table="t" title="t" defs={DEFS} rows={ROWS} rowKey={(r) => r.id} loading={false} empty="none" {...props} />
    </MemoryRouter>,
  );
}
const buttons = (html: string) => [...html.replace(/<!-- -->/g, "").matchAll(/<button type="button" class="th-sort">([^<]*)/g)].map((m) => m[1]);

describe("history table sort headers", () => {
  it("sorts nothing unless the table chooses a mode", () => {
    expect(buttons(render({}))).toEqual([]);
  });

  it("client mode: every column with a sortValue", () => {
    expect(buttons(render({ clientSort: true }))).toEqual(["trade", "net"]);
  });

  it("server mode: only the ids the reader sorts, whatever has a sortValue", () => {
    const html = render({ sort: { sort: "net", dir: "asc" }, onSort: () => undefined, serverSorts: ["net"] });
    expect(buttons(html)).toEqual(["net ▴"]);
    expect(html).toContain('aria-sort="ascending"');
    // The rows are the server's order, untouched.
    expect(html.indexOf(">a<")).toBeLessThan(html.indexOf(">b<"));
    expect(buttons(render({ onSort: () => undefined }))).toEqual([]);
  });
});

describe("the sort cycle", () => {
  it("goes desc, asc, then back to the table's own order; another column starts desc", () => {
    expect(nextSort({}, "net")).toEqual({ sort: "net", dir: "desc" });
    expect(nextSort({ sort: "net", dir: "desc" }, "net")).toEqual({ sort: "net", dir: "asc" });
    expect(nextSort({ sort: "net", dir: "asc" }, "net")).toEqual({});
    expect(nextSort({ sort: "net", dir: "asc" }, "fees")).toEqual({ sort: "fees", dir: "desc" });
  });
});
