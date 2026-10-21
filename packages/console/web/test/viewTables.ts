import type { ViewMode } from "../src/lightbox/navGroups";
import { setViewMode } from "../src/lib/viewMode";

/**
 * Helpers for the simple/detail column tests (2026-10-21): render under a view, then read every
 * table's headers and each body row's cell count, so a test can assert a column is present or absent
 * AND that no row has drifted out from under its headers.
 */

/** Run `fn` with the console in `view`, then put the default (detail) back. */
export function inView<T>(view: ViewMode, fn: () => T): T {
  setViewMode(view);
  try {
    return fn();
  } finally {
    setViewMode("detail");
  }
}

export interface TableShape {
  headers: string[];
  /** Each body row's width in columns (a colspan counts its span). */
  rowWidths: number[];
}

function plain(html: string): string {
  return html
    .replace(/<!-- -->/g, "")
    .replace(/<[^>]+>/g, "")
    .replace(/&amp;/g, "&")
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

/** Every table in the html (none nested: an expanded trade row is left closed in these tests). */
export function tables(html: string): TableShape[] {
  const out: TableShape[] = [];
  for (const m of html.matchAll(/<table[^>]*>([\s\S]*?)<\/table>/g)) {
    const body = m[1] ?? "";
    const head = /<thead>([\s\S]*?)<\/thead>/.exec(body)?.[1] ?? "";
    const headers = [...head.matchAll(/<th[^>]*>([\s\S]*?)<\/th>/g)].map((h) => plain(h[1] ?? "").replace(/ [▴▾]$/, ""));
    const tbody = /<tbody>([\s\S]*?)<\/tbody>/.exec(body)?.[1] ?? "";
    const rowWidths = [...tbody.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)].map((r) =>
      [...(r[1] ?? "").matchAll(/<td([^>]*)>/g)].reduce((n, td) => n + Number(/colSpan="(\d+)"|colspan="(\d+)"/i.exec(td[1] ?? "")?.slice(1).find(Boolean) ?? 1), 0),
    );
    out.push({ headers, rowWidths });
  }
  return out;
}

/** True when every body row is exactly as wide as the header row. */
export function aligned(t: TableShape): boolean {
  return t.rowWidths.every((w) => w === t.headers.length);
}
