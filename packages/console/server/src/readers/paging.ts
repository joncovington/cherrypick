import type { Paged } from "@console/shared";
import type { DatabaseHandle } from "./db.js";

/** Page sizes the tables offer, and the ceiling on anything a client asks for. */
export const PAGE_SIZES = [50, 100, 200, 500] as const;
export const MAX_PAGE = 500;
export const DEFAULT_PAGE = 100;

export type SortDir = "asc" | "desc";

export interface PageRequest {
  limit: number;
  offset: number;
  /** A column the table asked to sort by (2026-10-16). Honoured only where the reader names it in
   *  its own sort accessors; anything else keeps the reader's default order. */
  sort?: string;
  dir?: SortDir;
}

export const FIRST_PAGE: PageRequest = { limit: DEFAULT_PAGE, offset: 0 };

/** A client may ask for anything; what it gets is bounded here, not there. */
export function clampPage(p: PageRequest): PageRequest {
  const out: PageRequest = {
    limit: Math.min(Math.max(1, Math.trunc(p.limit)), MAX_PAGE),
    offset: Math.max(0, Math.trunc(p.offset)),
  };
  if (p.sort !== undefined && p.sort !== "") {
    out.sort = p.sort;
    out.dir = p.dir === "asc" ? "asc" : "desc";
  }
  return out;
}

/**
 * Read a page request off a query string. The prefix lets one endpoint carry
 * several independently paged tables (`booksOffset`, `positionsOffset`, `booksSort`).
 */
export function parsePage(q: unknown, prefix = ""): PageRequest {
  const query = (q ?? {}) as Record<string, unknown>;
  const key = (k: string): string => (prefix === "" ? k : `${prefix}${k[0]!.toUpperCase()}${k.slice(1)}`);
  const int = (k: string, fallback: number): number => {
    const n = Number(query[key(k)]);
    return Number.isFinite(n) ? Math.trunc(n) : fallback;
  };
  const sort = query[key("sort")];
  const dir = query[key("dir")];
  return clampPage({
    limit: int("limit", DEFAULT_PAGE),
    offset: int("offset", 0),
    ...(typeof sort === "string" ? { sort, dir: dir === "asc" ? "asc" : "desc" } : {}),
  });
}

export function emptyPage<T>(p: PageRequest = FIRST_PAGE): Paged<T> {
  const c = clampPage(p);
  return { rows: [], total: 0, offset: c.offset, limit: c.limit };
}

/**
 * A table's sortable columns, each read off the row AS SERVED -- the same figure the cell shows, so
 * a net derived in the reader (gross − every cost) sorts as that net and not as some ledger column
 * that only resembles it.
 */
export type SortAccessors<T> = Record<string, (row: T) => number | string | null>;

/**
 * Order rows by one accessor, nulls last in either direction. Stable, so rows the column cannot
 * tell apart keep the reader's own order (newest first, usually).
 */
export function sortBy<T>(rows: T[], get: (row: T) => number | string | null, dir: SortDir): T[] {
  const mul = dir === "asc" ? 1 : -1;
  return rows
    .map((row, i) => ({ row, i, v: get(row) }))
    .sort((a, b) => {
      if (a.v === null || b.v === null) return a.v === b.v ? a.i - b.i : a.v === null ? 1 : -1;
      const c = typeof a.v === "string" || typeof b.v === "string" ? String(a.v).localeCompare(String(b.v)) : a.v - b.v;
      return c === 0 ? a.i - b.i : mul * c;
    })
    .map((x) => x.row);
}

export interface PagedQuerySpec {
  columns: string;
  from: string;
  /** Already-composed SQL, without the WHERE keyword. Use "1=1" for unfiltered. */
  where: string;
  params: string[];
  orderBy: string;
}

/**
 * Count the matches and fetch one page of them, in that order and from the same
 * predicate — so the count always describes the window's own result set rather
 * than something adjacent to it.
 *
 * With a sort the reader names in `sorts`, every match is read and mapped, ordered by that
 * accessor, then sliced: the page is the right slice of the WHOLE sorted set, never one page
 * re-ordered on its own. The tables this serves hold hundreds of rows, not millions.
 */
export function pagedQuery<T>(
  db: DatabaseHandle,
  spec: PagedQuerySpec,
  page: PageRequest,
  map: (r: Record<string, unknown>) => T,
  sorts?: SortAccessors<T>,
): Paged<T> {
  const p = clampPage(page);
  const get = p.sort !== undefined ? sorts?.[p.sort] : undefined;
  if (get !== undefined) {
    const all = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT ${spec.columns} FROM ${spec.from} WHERE ${spec.where} ORDER BY ${spec.orderBy}`,
      )
      .all(...spec.params)
      .map(map);
    return pageArray(all, p, sorts);
  }
  const { limit, offset } = p;
  const total = Number(
    db.prepare<string[], { n: number }>(`SELECT COUNT(*) AS n FROM ${spec.from} WHERE ${spec.where}`).get(...spec.params)
      ?.n ?? 0,
  );
  const rows = db
    .prepare<string[], Record<string, unknown>>(
      `SELECT ${spec.columns} FROM ${spec.from} WHERE ${spec.where} ORDER BY ${spec.orderBy} LIMIT ? OFFSET ?`,
    )
    .all(...spec.params, String(limit), String(offset))
    .map(map);
  return { rows, total, offset, limit };
}

/**
 * Page an already-materialized list. Only for results the DB cannot page
 * itself — a browse that merges two stores, say — where the whole set must be
 * read to be ordered correctly. The total is still the true one. A sort the
 * reader names in `sorts` orders the whole list before the slice.
 */
export function pageArray<T>(rows: T[], page: PageRequest, sorts?: SortAccessors<T>): Paged<T> {
  const { limit, offset, sort, dir } = clampPage(page);
  const get = sort !== undefined ? sorts?.[sort] : undefined;
  const ordered = get !== undefined ? sortBy(rows, get, dir ?? "desc") : rows;
  return { rows: ordered.slice(offset, offset + limit), total: rows.length, offset, limit };
}
