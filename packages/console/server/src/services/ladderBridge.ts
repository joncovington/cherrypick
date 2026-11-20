import type { LadderArmRule, LadderArmSummary, LadderFund, LadderStatus, LadderSummary } from "@console/shared";
import { spawnModuleCliAsync } from "./moduleCli.js";

/**
 * The two ladder answers this package must not compute for itself, asked of the module's own
 * read-only CLI (`python -m cherrypick.ladder.cli`):
 *
 * - `status`: each fund's previous close and each arm's next buy level. Which close is the reference
 *   and how a chain anchors on its last lot are the module's `rules.py`; a copy here would drift.
 * - `summary`: each arm's lots, realised net, open lots at the bid and the group's 18-day counts.
 *
 * `readers/ladder.ts` reads the ledger directly for the lots. Each failure folds to
 * `{ ok: false, error }`, so the page says the module's read is unavailable instead of drawing an
 * empty table that looks like "nothing yet".
 */

const UNAVAILABLE = "ladder read unavailable — is cherrypick-ladder installed?";

type Json = Record<string, unknown>;

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const obj = (v: unknown): Json => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Json) : {});
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

export function normaliseStatus(j: Json): Omit<LadderStatus, "ok" | "error"> {
  const arms: Record<string, LadderArmRule> = {};
  for (const [name, a] of Object.entries(obj(j["arms"]))) {
    arms[name] = { entry: str(obj(a)["entry"]) ?? "", target: str(obj(a)["target"]) ?? "" };
  }
  const funds: Record<string, LadderFund> = {};
  for (const [symbol, f] of Object.entries(obj(j["funds"]))) {
    const raw = obj(f);
    funds[symbol] = {
      bid: num(raw["bid"]),
      ask: num(raw["ask"]),
      refClose: num(raw["ref_close"]),
      nextBuy: Object.fromEntries(Object.entries(obj(raw["next_buy"])).map(([arm, v]) => [arm, num(v)])),
    };
  }
  return {
    date: str(j["date"]),
    hours: str(j["hours"]),
    inHours: typeof j["in_hours"] === "boolean" ? j["in_hours"] : null,
    arms,
    funds,
  };
}

function armSummary(a: Json): LadderArmSummary {
  return {
    lots: num(a["lots"]) ?? 0,
    closed: num(a["closed"]) ?? 0,
    open: num(a["open"]) ?? 0,
    netPnl: num(a["net_pnl"]) ?? 0,
    grossPnl: num(a["gross_pnl"]) ?? 0,
    fees: num(a["fees"]) ?? 0,
    unrealisedAtBid: num(a["unrealised_at_bid"]) ?? 0,
    soldWithin18d: num(a["sold_within_18d"]) ?? 0,
    inHospital: num(a["in_hospital"]) ?? 0,
    medianHoldDays: num(a["median_hold_days"]),
    longestClosedHoldDays: num(a["longest_closed_hold_days"]),
    oldestOpenDays: num(a["oldest_open_days"]),
    symbols: strings(a["symbols"]),
  };
}

export function normaliseSummary(j: Json): Omit<LadderSummary, "ok" | "error"> {
  return { arms: Object.fromEntries(Object.entries(obj(j["arms"])).map(([arm, a]) => [arm, armSummary(obj(a))])) };
}

type Verb = "status" | "summary";
type Caller = (dbPath: string, verb: Verb) => Promise<{ ok: boolean; json: Json | null; error: string | null }>;

const realCaller: Caller = (dbPath, verb) => spawnModuleCliAsync(["-m", "cherrypick.ladder.cli", "--db", dbPath, verb], UNAVAILABLE);

let caller: Caller = realCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setLadderCaller(fn?: Caller): void {
  caller = fn ?? realCaller;
}

async function ask<T extends { ok: boolean; error: string | null }>(
  dbPath: string,
  verb: Verb,
  shape: (j: Json) => Omit<T, "ok" | "error">,
  failed: Omit<T, "ok" | "error">,
): Promise<T> {
  const res = await caller(dbPath, verb);
  if (!res.ok || res.json === null) return { ok: false, error: res.error, ...failed } as T;
  try {
    return { ok: true, error: null, ...shape(res.json) } as T;
  } catch (err) {
    return { ok: false, error: `${UNAVAILABLE} — ${(err as Error).message}`, ...failed } as T;
  }
}

export function readLadderStatus(dbPath: string): Promise<LadderStatus> {
  return ask<LadderStatus>(dbPath, "status", normaliseStatus, { date: null, hours: null, inHours: null, arms: {}, funds: {} });
}

export function readLadderSummary(dbPath: string): Promise<LadderSummary> {
  return ask<LadderSummary>(dbPath, "summary", normaliseSummary, { arms: {} });
}
