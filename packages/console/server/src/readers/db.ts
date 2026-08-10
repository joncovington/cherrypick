import fs from "node:fs";
import Database from "better-sqlite3";

export type DatabaseHandle = Database.Database;

/**
 * Open a module's SQLite store read-only, run `fn`, and close the handle.
 * Returns `fallback` when the DB doesn't exist or the read fails — module
 * stores may legitimately be absent (module not yet run on this machine).
 */
export function withReadOnlyDb<T>(path: string, fallback: T, fn: (db: Database.Database) => T): T {
  if (!fs.existsSync(path)) return fallback;
  let db: Database.Database | null = null;
  try {
    db = new Database(path, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    return fn(db);
  } catch {
    return fallback;
  } finally {
    db?.close();
  }
}

/**
 * Whether a column exists, for stores whose schema has moved over time — an
 * older DB (or the practice store) can be missing a column the current one has.
 */
export function hasColumn(db: Database.Database, table: string, column: string): boolean {
  return db
    .prepare<[], Record<string, unknown>>(`PRAGMA table_info(${table})`)
    .all()
    .some((c) => c["name"] === column);
}

export function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}
