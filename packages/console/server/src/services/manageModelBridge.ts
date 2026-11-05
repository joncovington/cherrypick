import type { ManageModelResult } from "@console/shared";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";
import { camelise } from "./pmccTrackerBridge.js";

/**
 * Model a trade, bridged from packages/manage (`python -m cherrypick.manage model`, 2026-11-04):
 * proposed legs on a held name, priced against the stored snapshot, with the payoff before and after
 * and the caps the trade would use. Bridged, not mirrored ("mirror a query, bridge a derivation"):
 * the pricing is manage's American model and its ruleset's caps. It reads and prices; it sends
 * nothing anywhere, and nothing here is an order path.
 */

const UNAVAILABLE = "trade model unavailable — packages/manage did not answer";

/** The leg text manage reads (`rollcheck.LEG`): "-1 2026-12-18 46P @ 1.05", the price optional. */
export const LEG = /^[+-]?\d+(\.\d+)? \d{4}-\d{2}-\d{2} \d+(\.\d+)?[CP]( @ \d+(\.\d+)?)?$/;
export const UNDERLYING = /^[A-Z][A-Z0-9./]{0,9}$/;
const MAX_LEGS = 6;

export type ModelCaller = (argv: string[]) => SpawnedJson | Promise<SpawnedJson>;
const spawnCaller: ModelCaller = (argv) => spawnModuleCliAsync(argv, UNAVAILABLE, [0, 1]);
let caller: ModelCaller = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setManageModelCaller(fn?: ModelCaller): void {
  caller = fn ?? spawnCaller;
}

function refused(error: string): ManageModelResult {
  return {
    ok: false,
    error,
    session: null,
    underlying: null,
    spot: null,
    model: null,
    legs: [],
    net: null,
    delta: null,
    thetaDay: null,
    worstCase: null,
    worstCaseAt: null,
    unbounded: false,
    accountBasis: null,
    caps: [],
    ivIndex: null,
    hv30Day: null,
    before: null,
    after: null,
  };
}

/** Validate, run manage, camelise. A bad request is refused here, before any process starts. */
export async function readManageModel(underlying: string, legs: string[]): Promise<ManageModelResult> {
  const name = underlying.toUpperCase();
  if (!UNDERLYING.test(name)) return refused("the underlying must be a ticker");
  if (legs.length === 0) return refused("no leg to model");
  if (legs.length > MAX_LEGS) return refused(`at most ${MAX_LEGS} legs`);
  const bad = legs.find((l) => !LEG.test(l));
  if (bad !== undefined) return refused(`cannot read leg "${bad}"; expected like "-1 2026-12-18 46P @ 1.05"`);
  const res = await caller(["-m", "cherrypick.manage", "model", "--underlying", name, ...legs.flatMap((l) => ["--leg", l])]);
  if (!res.ok || res.json === null) return refused(res.error ?? UNAVAILABLE);
  const out = camelise(res.json) as Partial<ManageModelResult>;
  if (out.ok !== true) return refused(typeof out.error === "string" ? out.error : UNAVAILABLE);
  return { ...refused(""), ...out, ok: true, error: null } as ManageModelResult;
}
