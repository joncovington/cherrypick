import fs from "node:fs";
import Database from "better-sqlite3";

export type DatabaseHandle = Database.Database;

/**
 * Open a module's SQLite store read-only, run `fn`, and close the handle.
 * Returns `fallback` when the DB doesn't exist or the read fails — module
 * stores may legitimately be absent (module not yet run on this machine).
 */
/**
 * Pooled read-only handles, keyed by path and recycled when the file underneath changes.
 *
 * Opening a handle costs ~2.2ms against ~0.025ms for a query on an already-open one (measured on
 * the flies ledger), and the SPA polls several endpoints at once, so the open/close was a real
 * share of a short request.
 *
 * **Recycled on the file stamp, not held forever.** A module's migration is additive and lands
 * while this process is running; a handle opened before it would keep serving the older schema
 * for the life of the server. The stamp covers the -wal too, so a write that has not yet
 * checkpointed still counts as a change.
 *
 * Idle handles do NOT block WAL checkpointing: better-sqlite3 runs each statement in its own
 * implicit transaction and releases the read lock when it returns, so what is retained here is an
 * open FILE, not an open read transaction. Holding a transaction open across calls would starve
 * the checkpointer, which is why nothing here does.
 */
const handlePool = new Map<string, { db: Database.Database; stamp: string }>();

function closeQuietly(db: Database.Database): void {
  try {
    db.close();
  } catch {
    /* already gone */
  }
}

/** Drop a pooled handle — used when a read throws, so a wedged handle is never reused. */
function evict(path: string): void {
  const held = handlePool.get(path);
  if (held !== undefined) {
    handlePool.delete(path);
    closeQuietly(held.db);
  }
}

/**
 * Reader failures, kept because the fallback below makes them invisible otherwise.
 *
 * `withReadOnlyDb` returns `fallback` for BOTH "this module has never run here" and "the query
 * threw", and the first is legitimate — so the fallback stays. What was missing is that the second
 * left no trace anywhere: not on the wire (the request still returns 200), not on the page, and not
 * in the log. Two real defects hid in exactly that gap. `/api/flies/meta` served
 * `{arms: [], dates: [], symbols: []}` from one bad column in a UNION, and a day resolver naming a
 * table an older ledger lacks read as "no latest session", so a tab meant to show one day answered
 * for every day in its era. Both looked healthy.
 *
 * This does NOT change what any caller receives — separating the two return paths would change
 * semantics at ~65 call sites and wants its own landing. It makes the second case observable:
 * recorded here, logged if a logger was installed, and surfaced by `/api/health`.
 */
export interface ReaderFailure {
  path: string;
  error: string;
  count: number;
  lastAt: string;
}

const readerFailures = new Map<string, ReaderFailure>();
let onReaderFailure: ((message: string) => void) | undefined;

/**
 * Install a logger for swallowed read failures. Called once at startup with Fastify's own logger —
 * `db.ts` has no request context and 65 call sites do not pass one, so injection beats threading it
 * through. Optional by design: a test or a CLI that never installs one still records.
 */
export function setReaderFailureLogger(log: ((message: string) => void) | undefined): void {
  onReaderFailure = log;
}

/** Every store whose reads have thrown since start, most recent first. */
export function listReaderFailures(): ReaderFailure[] {
  return [...readerFailures.values()].sort((a, b) => b.lastAt.localeCompare(a.lastAt));
}

export function clearReaderFailures(): void {
  readerFailures.clear();
}

function recordReaderFailure(path: string, err: unknown): void {
  const error = err instanceof Error ? err.message : String(err);
  const prior = readerFailures.get(path);
  readerFailures.set(path, {
    path,
    error,
    count: (prior?.count ?? 0) + 1,
    lastAt: new Date().toISOString(),
  });
  // First occurrence per (path, message) is the one worth a line; a wedged reader polled every few
  // seconds would otherwise fill the log with the same error and bury it.
  if (prior === undefined || prior.error !== error) {
    onReaderFailure?.(`reader failed, serving fallback: ${path} — ${error}`);
  }
}

/**
 * What a read actually did, for callers that need to tell the three cases apart.
 *
 * `withReadOnlyDb` collapses all three into one value and ~65 call sites depend on that, so it is
 * unchanged and stays the default. This is the opt-in form: a reader whose EMPTINESS IS MEANINGFUL —
 * where "no rows" and "the query threw" would be read differently by whoever sees the page — takes
 * this and says which it got. Migration is per call site and needs no sweep.
 */
export type ReadOutcome<T> =
  | { status: "ok"; value: T }
  | { status: "absent" }
  | { status: "failed"; error: string };

/**
 * The single implementation. `withReadOnlyDb` is a wrapper over this rather than a second copy of
 * the pooling, stamping and eviction logic — two of those would be two chances to disagree about
 * when a handle is recycled, which is the class of bug the stamp exists to prevent.
 */
export function readOnlyDb<T>(path: string, fn: (db: Database.Database) => T): ReadOutcome<T> {
  // An absent store is NOT a failure: a module may legitimately never have run on this machine.
  // Deliberately returned before the try, so it is not recorded as one.
  if (!fs.existsSync(path)) return { status: "absent" };
  try {
    const stamp = fileStamp(path);
    let held = handlePool.get(path);
    if (held !== undefined && held.stamp !== stamp) {
      // The store moved under us -- reopen so a migration is never served from a stale handle.
      handlePool.delete(path);
      closeQuietly(held.db);
      held = undefined;
    }
    if (held === undefined) {
      const db = new Database(path, { readonly: true, fileMustExist: true });
      db.pragma("busy_timeout = 2000");
      held = { db, stamp };
      handlePool.set(path, held);
    }
    return { status: "ok", value: fn(held.db) };
  } catch (err) {
    evict(path);
    recordReaderFailure(path, err);
    return { status: "failed", error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Open a module's SQLite store read-only, run `fn`, and return `fallback` when the store is absent
 * OR the read throws. The contract every existing call site is written against; unchanged.
 */
export function withReadOnlyDb<T>(path: string, fallback: T, fn: (db: Database.Database) => T): T {
  const outcome = readOnlyDb(path, fn);
  return outcome.status === "ok" ? outcome.value : fallback;
}

/** Close every pooled handle. For tests and shutdown; a reopen is transparent to callers. */
export function closePooledDbs(): void {
  for (const [p] of handlePool) evict(p);
}

/**
 * A cheap fingerprint of a store's on-disk state, for caching things that only change when the file
 * does. Both the database and its write-ahead log are stamped: these stores are WAL, so a write —
 * a migration included — lands in the `-wal` and can leave the main file's mtime untouched for a
 * long time. Stamping the database alone would cache a schema that has already moved.
 */
function fileStamp(p: string): string {
  let s = "";
  for (const f of [p, `${p}-wal`]) {
    try {
      const st = fs.statSync(f);
      s += `${st.mtimeMs}:${st.size};`;
    } catch {
      s += "-;";
    }
  }
  return s;
}

const columnCache = new Map<string, { stamp: string; columns: Set<string> }>();

/**
 * Every spelling a variant's column has had, canonical first — core.db.ARM_COLUMNS. A module's
 * ledger renames its column to `arm` in a window of its own (bwb/calendars/curve/pmcc on
 * 2026-09-23; meic and earnings each later), so a reader cannot know which side of that window the
 * file it opened is on, and a live ledger can sit on the other side from its paper twin.
 */
export const ARM_COLUMNS = ["arm", "risk_profile", "book", "profile"] as const;

/**
 * The name `table`'s arm column goes by in THIS file. Callers alias it at the SELECT
 * (`${col} AS arm`). Throws when there is none: `readOnlyDb` then records a failure, which
 * `/api/health` shows — where returning a guess would reproduce the 2026-09-23 defect, a reader
 * asking for a column that moved and its error swallowed into an empty-but-200 payload.
 */
export function armColumnOf(db: Database.Database, table: string): string {
  const found = findArmColumn(db, table);
  if (found === null) throw new Error(`${table}: no arm column (looked for ${ARM_COLUMNS.join(", ")})`);
  return found;
}

/** `armColumnOf` for a reader that already treats an untagged ledger as a legitimate state. */
export function findArmColumn(db: Database.Database, table: string): string | null {
  for (const name of ARM_COLUMNS) if (hasColumn(db, table, name)) return name;
  return null;
}

/**
 * Whether a column exists, for stores whose schema has moved over time — an
 * older DB (or the practice store) can be missing a column the current one has.
 *
 * Memoised per (file, table) against the stamp above: a schema read costs ~10x a stat, and some
 * endpoints ask this four times per request while the answer changes only when a module migrates.
 */
export function hasColumn(db: Database.Database, table: string, column: string): boolean {
  const key = `${db.name} ${table}`;
  const stamp = fileStamp(db.name);
  let hit = columnCache.get(key);
  if (hit === undefined || hit.stamp !== stamp) {
    const columns = new Set(
      db
        .prepare<[], Record<string, unknown>>(`PRAGMA table_info(${table})`)
        .all()
        .map((c) => String(c["name"])),
    );
    hit = { stamp, columns };
    columnCache.set(key, hit);
  }
  return hit.columns.has(column);
}

export function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

export function readJson(p: string): Record<string, unknown> | null {
  try {
    return JSON.parse(fs.readFileSync(p, "utf-8")) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** A JSON object, or `{}` for anything else. Arrays are NOT objects here.
 *
 * The seven local copies of this had drifted into two versions: calendars/review excluded arrays,
 * pmcc did not. Excluding them is the reconciled behaviour and the correct one -- every call site
 * reads NAMED keys off the result, and an array reached through the permissive version returned
 * numeric keys that made every named lookup undefined anyway. Same answer, arrived at honestly.
 */
export function obj(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

/** `num`, but also parsing numeric STRINGS.
 *
 * A deliberately separate export rather than a loosening of `num`. Broker and watchlist payloads
 * quote their numbers, so screener/ttWatchlists need the coercion; a ledger column that arrives as
 * a string is a bug the strict version should keep surfacing. Folding these two together would
 * silence that.
 */
export function numLoose(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = Number.parseFloat(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

/** `str`, but an empty string is null -- for fields where "" means absent rather than empty. */
export function strNonEmpty(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

/**
 * Memoise a derived read against the store's on-disk stamp.
 *
 * For reads that are expensive to BUILD rather than to fetch. The flies timeline replays every
 * position at every recorded tick, which is ~140ms of pure computation for one session, and the
 * page re-polls it every 30s. The ledger only changes when the module writes — every 15s in
 * session, and not at all outside one — so recomputing on a poll that landed between writes
 * produces a byte-identical answer at full cost.
 *
 * Keyed on the same stamp the handle pool recycles against, so "the file moved" means exactly one
 * thing everywhere in this module. No TTL: a stamp change is the only thing that can alter the
 * answer, and a timer would either expire early (wasted work) or late (a stale read), where the
 * stamp is simply correct.
 */
const derivedCache = new Map<string, { stamp: string; value: unknown }>();

/**
 * Bounded, because the values here are large and the KEYS are user-driven: the flies timeline is
 * ~1MB per session and its key carries the day, so browsing back through a month of sessions would
 * otherwise pin every one of them for the life of the process. Insertion-ordered eviction (a Map
 * iterates in insertion order, and a re-cached entry is re-inserted below), which is LRU enough for
 * a handful of entries and costs nothing to reason about.
 */
const DERIVED_CACHE_MAX = 16;

export function memoOnStore<T>(dbPath: string, key: string, build: () => T): T {
  const stamp = fileStamp(dbPath);
  const hit = derivedCache.get(key);
  if (hit !== undefined && hit.stamp === stamp) {
    derivedCache.delete(key); // re-insert so the freshly used entry is evicted last
    derivedCache.set(key, hit);
    return hit.value as T;
  }
  const value = build();
  derivedCache.delete(key);
  derivedCache.set(key, { stamp, value });
  while (derivedCache.size > DERIVED_CACHE_MAX) {
    const oldest = derivedCache.keys().next();
    if (oldest.done === true) break;
    derivedCache.delete(oldest.value);
  }
  return value;
}

/** Drop every memoised read. For tests; a rebuild is transparent to callers. */
export function clearMemoOnStore(): void {
  derivedCache.clear();
}

/** Whether a table exists. Memoised alongside hasColumn, against the same file stamp. */
export function hasTable(db: Database.Database, table: string): boolean {
  const key = `${db.name}  table:${table}`;
  const stamp = fileStamp(db.name);
  let hit = columnCache.get(key);
  if (hit === undefined || hit.stamp !== stamp) {
    const row = db
      .prepare<[string], { name: string }>("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?")
      .get(table);
    hit = { stamp, columns: new Set(row === undefined ? [] : [table]) };
    columnCache.set(key, hit);
  }
  return hit.columns.has(table);
}

/**
 * The suite's declared measurement era, from the orchestrator config's `data_epoch`.
 *
 * One source, deliberately: `data_epoch` is the suite-wide boundary lever (2026-08-21 is the
 * advisor-era cutover), already enforced in the orchestrator's calibrate. Read surfaces that
 * aggregate across sessions bound themselves to it so an "era total" here can never disagree with
 * the date the promotion evidence clock was reset to. Null date = no declared era = read
 * everything, which is what the suite meant before the key existed.
 */
export function suiteEra(orchestratorConfigPath: string): { from: string | null; note: string | null } {
  const doc = readJson(orchestratorConfigPath);
  const epoch = doc?.["data_epoch"];
  if (typeof epoch !== "object" || epoch === null) return { from: null, note: null };
  const e = epoch as Record<string, unknown>;
  return {
    from: typeof e["date"] === "string" && e["date"] !== "" ? e["date"] : null,
    note: typeof e["note"] === "string" ? e["note"] : null,
  };
}
