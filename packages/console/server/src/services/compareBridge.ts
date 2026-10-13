import path from "node:path";
import type { CompareDoc, ComparePayload, CompareScope } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { memoOnStore } from "../readers/db.js";
import { spawnModuleCli } from "./moduleCli.js";

/**
 * Every arm beside its control, read from the module that owns the statistics.
 *
 * `python -m cherrypick.core.compare --json` reads each module's closed rows through
 * `core.ledgers` (the one home for what net, cost and session mean per schema), bounds them to the
 * module's own evidence window, and hands each arm to `core.stats`: the session-bootstrap interval,
 * the paired difference with its control, the one-sided t bound, the exact sign test and the
 * sessions-until projection. A TypeScript copy of any of that would be a second derivation free to
 * drift from the validated one -- "mirror a query, bridge a derivation" (console CLAUDE.md). Same
 * pattern as `metricsBridge.ts`/`screenBridge.ts`; field names stay the bridge's, verbatim.
 */

const UNAVAILABLE =
  "arm comparison unavailable — cherrypick-core must be installed with its compare module (pip install -e packages/core)";

/** The document's schema this page reads. A newer one is refused rather than half-rendered. */
const SCHEMA = 1;

export type CompareResult = Omit<ComparePayload, "scope">;

/** Shape the CLI's raw JSON: the CLI's own `{ok: false}`, a schema this page cannot read, and a
 *  document without a module list are all failures with a reason -- never an empty table. */
export function shapeCompare(json: Record<string, unknown> | null, error: string | null): CompareResult {
  if (json === null) return { ok: false, data: null, error: error ?? `${UNAVAILABLE} — no response` };
  if (json["ok"] !== true) {
    const err = json["error"];
    return { ok: false, data: null, error: typeof err === "string" ? err : `${UNAVAILABLE} — refused` };
  }
  if (json["schema"] !== SCHEMA) {
    return {
      ok: false,
      data: null,
      error: `the compare bridge wrote schema ${String(json["schema"])}; this console reads schema ${String(SCHEMA)}`,
    };
  }
  if (!Array.isArray(json["modules"])) {
    return { ok: false, data: null, error: "the compare bridge's document has no module list" };
  }
  const { ok: _ok, ...doc } = json;
  return { ok: true, data: doc as unknown as CompareDoc, error: null };
}

function spawnCaller(scope: CompareScope, session: string): CompareResult {
  const res = spawnModuleCli(["-m", "cherrypick.core.compare", "--json", "--scope", scope, "--session", session], UNAVAILABLE);
  // The CLI's own `{"ok": false, "error": ...}` exits 1; spawnModuleCli carries that reason into
  // `error` from stdout, so a refusal and a missing interpreter both arrive here as `res.error`.
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error };
  return shapeCompare(res.json, res.error);
}

let caller = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setCompareCaller(fn?: typeof spawnCaller): void {
  caller = fn ?? spawnCaller;
}

/** The New York date: sessions are New York trading days, and the bridge is told which one. */
export function nySession(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(now);
}

/**
 * Every store the bridge reads: each module's paper ledger (rows, and the measurement breaks
 * journaled beside them) and the advisor's store (which names an advised arm's control). The memo
 * is stamped on all of them, so a trade closing in any module, or a break journaled, is a miss.
 */
export function compareStores(config: ConsoleConfig): string[] {
  const p = config.paths;
  const dirs = [p.fliesDir, p.meicDir, p.bwbDir, p.curveDir, p.calendarsDir, p.pmccDir, p.earningsDir, p.contangoDir];
  return [...dirs.map((d) => path.join(d, "paper_trades.db")), path.join(p.advisorDir, "advisor.db")];
}

/**
 * The comparison for one scope, memoised on the stores' stamps and the session. The session is in
 * the key because a break dated in the future binds from its own date: the same ledgers read on a
 * later day can bound a different era. A failure is memoised too, until a store moves or the day
 * turns -- a missing interpreter does not fix itself between polls.
 */
export function readCompare(config: ConsoleConfig, scope: CompareScope, session: string = nySession()): ComparePayload {
  const result = memoOnStore(compareStores(config), `compare:${scope}:${session}`, () => caller(scope, session));
  return { ...result, scope };
}
