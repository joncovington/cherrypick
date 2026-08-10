import type { Paged } from "@console/shared";
import type { DatabaseHandle } from "./db.js";

/** Page sizes the tables offer, and the ceiling on anything a client asks for. */
export const PAGE_SIZES = [50, 100, 200, 500] as const;
export const MAX_PAGE = 500;
export const DEFAULT_PAGE = 100;

export interface PageRequest {
  limit: number;
  offset: number;
}

export const FIRST_PAGE: PageRequest = { limit: DEFAULT_PAGE, offset: 0 };

/** A client may ask for anything; what it gets is bounded here, not there. */
export function clampPage(p: PageRequest): PageRequest {
  return {
    limit: Math.min(Math.max(1, Math.trunc(p.limit)), MAX_PAGE),
    offset: Math.max(0, Math.trunc(p.offset)),
  };
}

/**
 * Read a page request off a query string. The prefix lets one endpoint carry
 * several independently paged tables (`booksOffset`, `positionsOffset`).
 */
export function parsePage(q: unknown, prefix = ""): PageRequest {
  const query = (q ?? {}) as Record<string, unknown>;
  const key = (k: string): string => (prefix === "" ? k : `${prefix}${k[0]!.toUpperCase()}${k.slice(1)}`);
  const int = (k: string, fallback: number): number => {
    const n = Number(query[key(k)]);
    return Number.isFinite(n) ? Math.trunc(n) : fallback;
  };
  return clampPage({ limit: int("limit", DEFAULT_PAGE), offset: int("offset", 0) });
}

export function emptyPage<T>(p: PageRequest = FIRST_PAGE): Paged<T> {
  const c = clampPage(p);
  return { rows: [], total: 0, offset: c.offset, limit: c.limit };
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
 */
export function pagedQuery<T>(
  db: DatabaseHandle,
  spec: PagedQuerySpec,
  page: PageRequest,
  map: (r: Record<string, unknown>) => T,
): Paged<T> {
  const { limit, offset } = clampPage(page);
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
 * read to be ordered correctly. The total is still the true one.
 */
export function pageArray<T>(rows: T[], page: PageRequest): Paged<T> {
  const { limit, offset } = clampPage(page);
  return { rows: rows.slice(offset, offset + limit), total: rows.length, offset, limit };
}
