import type {
  PmccBridged,
  PmccTracker,
  PmccTrackerIndexRow,
  PmccWeeklyRow,
} from "@console/shared";
import { readOnlyDb } from "../readers/db.js";
import { spawnModuleCli } from "./moduleCli.js";

/**
 * The PMCC position tracker, bridged from the module's own `analytics.tracker` /
 * `tracker_index` / `weekly_by_arm` (`python -m cherrypick.pmcc.cli --db PATH tracker ...`).
 *
 * Bridged, not mirrored, by the console's own rule ("mirror a query, bridge a derivation"): the
 * tracker values a position as of any instant -- every leg at its close or its mark then, delivered
 * shares, every cost at its own timestamp -- and a TypeScript copy of that would be a second
 * opinion on what a position is worth. The JSON is the module's; this file only camelises its keys.
 *
 * A position's tracker only changes when the loop marks it or trades it, so it is memoised on the
 * position's latest mark and latest leg write -- a 30-second refetch of an idle position spawns no
 * python at all. The index and the weekly A/B use a short TTL.
 */

const UNAVAILABLE = "pmcc tracker unavailable — the pmcc module's CLI did not answer";

export type TrackerCaller = (argv: string[]) => { ok: boolean; json: Record<string, unknown> | null; error: string | null };

const spawnCaller: TrackerCaller = (argv) => spawnModuleCli(argv, UNAVAILABLE);
let caller: TrackerCaller = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setPmccTrackerCaller(fn?: TrackerCaller): void {
  caller = fn ?? spawnCaller;
  memo.clear();
}

function camelKey(key: string): string {
  return key.replace(/_([a-z0-9])/g, (_m, c: string) => c.toUpperCase());
}

/** snake_case keys to camelCase, all the way down. Values are untouched. */
export function camelise(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(camelise);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([k, v]) => [camelKey(k), camelise(v)]),
    );
  }
  return value;
}

function run<T>(argv: string[], pick: (json: Record<string, unknown>) => unknown): PmccBridged<T> {
  const res = caller(argv);
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error };
  if (res.json["ok"] !== true) {
    const reason = res.json["reason"] ?? res.json["error"];
    return { ok: false, data: null, error: typeof reason === "string" ? reason : `${UNAVAILABLE} — refused` };
  }
  return { ok: true, data: camelise(pick(res.json)) as T, error: null };
}

const memo = new Map<string, { stamp: string; at: number; value: PmccBridged<unknown> }>();
const TTL_MS = 30_000;

/** What changes a position's tracker: its latest mark and its latest leg write. */
function positionStamp(dbPath: string, positionId: string): string | null {
  const out = readOnlyDb(dbPath, (db) => {
    const mark = db
      .prepare<[string], { t: number | null }>("SELECT MAX(marked_at) AS t FROM pmcc_marks WHERE position_id = ?")
      .get(positionId);
    const leg = db
      .prepare<[string], { u: string | null }>("SELECT MAX(updated_at) AS u FROM pmcc_legs WHERE position_id = ?")
      .get(positionId);
    const pos = db
      .prepare<[string], { u: string | null }>("SELECT updated_at AS u FROM pmcc_positions WHERE position_id = ?")
      .get(positionId);
    return `${mark?.t ?? ""}|${leg?.u ?? ""}|${pos?.u ?? ""}`;
  });
  return out.status === "ok" ? out.value : null;
}

export function readPmccTracker(dbPath: string, positionId: string, now = Date.now()): PmccBridged<PmccTracker> {
  const key = `tracker|${dbPath}|${positionId}`;
  const stamp = positionStamp(dbPath, positionId);
  const hit = memo.get(key);
  if (hit !== undefined && stamp !== null && hit.stamp === stamp && now - hit.at < 10 * TTL_MS) {
    return hit.value as PmccBridged<PmccTracker>;
  }
  const value = run<PmccTracker>(
    ["-m", "cherrypick.pmcc.cli", "--db", dbPath, "tracker", "--position", positionId],
    (json) => json["tracker"],
  );
  if (stamp !== null) memo.set(key, { stamp, at: now, value });
  return value;
}

function ttl<T>(key: string, now: number, compute: () => PmccBridged<T>): PmccBridged<T> {
  const hit = memo.get(key);
  if (hit !== undefined && now - hit.at < TTL_MS) return hit.value as PmccBridged<T>;
  const value = compute();
  memo.set(key, { stamp: "", at: now, value });
  return value;
}

export function readPmccTrackerIndex(dbPath: string, now = Date.now()): PmccBridged<PmccTrackerIndexRow[]> {
  return ttl(`index|${dbPath}`, now, () =>
    run<PmccTrackerIndexRow[]>(["-m", "cherrypick.pmcc.cli", "--db", dbPath, "tracker-index"], (json) => json["positions"]),
  );
}

export function readPmccWeekly(dbPath: string, era: string | null, now = Date.now()): PmccBridged<PmccWeeklyRow[]> {
  const argv = ["-m", "cherrypick.pmcc.cli", "--db", dbPath, "weekly", ...(era !== null ? ["--era", era] : [])];
  return ttl(`weekly|${dbPath}|${era ?? ""}`, now, () =>
    run<PmccWeeklyRow[]>(argv, (json) => (json["weekly"] as Record<string, unknown> | undefined)?.["rows"] ?? []),
  );
}

/** A position id as the module writes it (`book.position_id`): SYMBOL:arm:YYYY-MM-DD, the arm
 *  possibly an `advised:<experiment>` tag. Anything else never reaches the subprocess. */
export const POSITION_ID = /^[A-Z0-9.]{1,10}:[a-z0-9_:-]{1,80}:\d{4}-\d{2}-\d{2}$/;
