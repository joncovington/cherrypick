import { spawnSync } from "node:child_process";

/**
 * The two calendars answers this package must not compute for itself.
 *
 * `readers/calendars.ts` reads that module's ledger directly, the way every other reader here does.
 * These two are different in kind, and both would be re-derivations rather than reads:
 *
 * - **The exit-policy table.** It is not a query — it is a tick-by-tick replay of twelve candidate
 *   exit rules over the recorded mark path, priced through the module's own cost stack, and it
 *   arrives welded to the validation that re-derives the `control` policy from the control book's
 *   own marks and checks it against that book's real recorded net to the cent. The module's seventh
 *   honesty rule is that the ranking never travels without that reason to believe it. A TypeScript
 *   second implementation would be free to drift from the Python one in exactly the direction that
 *   makes the table look better than it is, and the validation could not catch it — it would be
 *   validating the wrong derivation.
 * - **The week plan.** `clock.week_plan` is holiday-calendar arithmetic (Monday, or the next
 *   trading day; Friday, or Thursday on a Good Friday week; the following Monday, shifted forward),
 *   and the structure tag it produces is the key results are grouped by. Re-implementing the NYSE
 *   calendar here to label a card would be a second calendar free to disagree with the one the
 *   module trades off.
 *
 * Same bridging pattern and the same reason as `configBridge.ts` and `screenBridge.ts`: the
 * authority stays in one place and this package renders it. Both verbs memoise, because both are
 * cheap to want on every poll and expensive to compute — and neither moves on a 15s timescale. The
 * week plan takes no clock at all, only a date, so it can only change at an ET date boundary; the
 * policy table can only change when a week completes.
 */

const UNAVAILABLE =
  "calendars analytics unavailable — the calendars package must be installed (pip install -e packages/calendars)";

interface Spawned {
  ok: boolean;
  json: Record<string, unknown> | null;
  error: string | null;
}

function spawnCli(verb: string): Spawned {
  let out;
  try {
    out = spawnSync("python", ["-m", "cherrypick.calendars.cli", verb], {
      encoding: "utf-8",
      timeout: 30_000,
      windowsHide: true,
    });
  } catch (err) {
    return { ok: false, json: null, error: `${UNAVAILABLE} (${(err as Error).message})` };
  }
  if (out.error !== undefined) return { ok: false, json: null, error: `${UNAVAILABLE} (${out.error.message})` };
  if (out.status !== 0) {
    const detail = (out.stderr ?? "").trim().split(/\r?\n/).pop() ?? `exit ${String(out.status)}`;
    return { ok: false, json: null, error: `${UNAVAILABLE} — ${detail}` };
  }
  try {
    return { ok: true, json: JSON.parse(out.stdout.trim()) as Record<string, unknown>, error: null };
  } catch {
    return { ok: false, json: null, error: `${UNAVAILABLE} — unparseable response` };
  }
}

let caller = spawnCli;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setCalendarsCaller(fn?: typeof spawnCli): void {
  caller = fn ?? spawnCli;
}

// The plan is date arithmetic and the table only moves when a week finishes, so both TTLs are long
// on purpose. The page polls; these must not turn a poll into a subprocess.
const PLAN_TTL_MS = 600_000;
const POLICIES_TTL_MS = 300_000;

const cache = new Map<string, { at: number; value: Spawned }>();

function memoised(verb: string, ttl: number, now: number): Spawned {
  const hit = cache.get(verb);
  if (hit !== undefined && now - hit.at < ttl) return hit.value;
  const value = caller(verb);
  cache.set(verb, { at: now, value });
  return value;
}

/** Drop the memoised responses. Tests only. */
export function resetCalendarsCache(): void {
  cache.clear();
}

export interface CalendarsPlanResult {
  plan: {
    weekOf: string;
    entrySession: string;
    frontExpiration: string;
    backExpiration: string;
    structure: string;
  } | null;
  error: string | null;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function obj(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

/** `cli status`'s `week_plan` — the anchors for the week the NEXT entry belongs to. */
export function readCalendarsPlan(now = Date.now()): CalendarsPlanResult {
  const res = memoised("status", PLAN_TTL_MS, now);
  if (!res.ok || res.json === null) return { plan: null, error: res.error };
  const p = res.json["week_plan"];
  // A null week_plan is a real answer (a calendar that cannot produce a week), not a failure.
  if (p === null || p === undefined) return { plan: null, error: null };
  const raw = obj(p);
  const weekOf = str(raw["week_of"]);
  const entrySession = str(raw["entry_session"]);
  const front = str(raw["front_expiration"]);
  const back = str(raw["back_expiration"]);
  const structure = str(raw["structure"]);
  if (weekOf === null || entrySession === null || front === null || back === null || structure === null) {
    return { plan: null, error: null };
  }
  return {
    plan: { weekOf, entrySession, frontExpiration: front, backExpiration: back, structure },
    error: null,
  };
}

export interface PolicyBucket {
  structure: string;
  weeks: number;
  derivable: number;
  totalNet: number | null;
  avgNet: number | null;
  winRate: number | null;
  worst: { weekOf: string; netPnl: number } | null;
}

export interface PoliciesResult {
  ok: boolean;
  error: string | null;
  weeksConsidered: number;
  caveat: string | null;
  policies: Array<{ policy: string; buckets: PolicyBucket[] }>;
  validation: {
    compared: number;
    ok: boolean;
    mismatches: Array<{
      weekOf: string;
      book: string;
      derivedNet: number | null;
      realNet: number | null;
      diff: number | null;
      reason: string | null;
    }>;
  } | null;
}

/** `cli policies` — `exit_policies.comparison_table`, validation included by construction. */
export function readCalendarsPolicies(now = Date.now()): PoliciesResult {
  const empty: PoliciesResult = {
    ok: false,
    error: null,
    weeksConsidered: 0,
    caveat: null,
    policies: [],
    validation: null,
  };
  const res = memoised("policies", POLICIES_TTL_MS, now);
  if (!res.ok || res.json === null) return { ...empty, error: res.error };

  const table = obj(res.json["policies"]);
  const byPolicy = obj(table["policies"]);
  const policies = Object.entries(byPolicy).map(([policy, structures]) => ({
    policy,
    buckets: Object.entries(obj(structures)).map(([structure, bucket]) => {
      const b = obj(bucket);
      const worst = obj(b["worst"]);
      const worstWeek = str(worst["week_of"]);
      const worstNet = numOrNull(worst["net_pnl"]);
      return {
        structure,
        weeks: Number(b["weeks"] ?? 0),
        derivable: Number(b["derivable"] ?? 0),
        totalNet: numOrNull(b["total_net"]),
        avgNet: numOrNull(b["avg_net"]),
        winRate: numOrNull(b["win_rate"]),
        worst: worstWeek === null || worstNet === null ? null : { weekOf: worstWeek, netPnl: worstNet },
      };
    }),
  }));

  const v = obj(table["validation"]);
  const mismatches = Array.isArray(v["mismatches"]) ? v["mismatches"] : [];
  return {
    ok: true,
    error: null,
    weeksConsidered: Number(table["weeks_considered"] ?? 0),
    caveat: str(table["caveat"]),
    policies,
    validation: {
      compared: Number(v["compared"] ?? 0),
      ok: v["ok"] === true,
      mismatches: mismatches.map((m) => {
        const row = obj(m);
        return {
          weekOf: str(row["week_of"]) ?? "",
          book: str(row["book"]) ?? "",
          derivedNet: numOrNull(row["derived_net"]),
          realNet: numOrNull(row["real_net"]),
          diff: numOrNull(row["diff"]),
          reason: str(row["reason"]),
        };
      }),
    },
  };
}
