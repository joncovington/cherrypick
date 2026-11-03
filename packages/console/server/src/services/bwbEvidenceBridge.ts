import path from "node:path";
import type {
  BridgedResult,
  BwbEntryContext,
  BwbIvPremium,
  BwbLiveGap,
  BwbRegimeSplit,
  BwbVolEvidence,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { AsyncMemo } from "./asyncMemo.js";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * bwb's own evidence reads, asked of the module (`python -m cherrypick.bwb.cli`):
 *
 * - **`live-gap`** (`analytics.live_gap`): the live arm's sessions against paper's same arm --
 *   the broker's entry credit against the modelled one, whether the add-on fired on both sides,
 *   the fee reconciliation (`fee_reconcile`'s modelled snapshot against the broker's), net; and
 *   the live refusals, each named with the configured gate it belongs to (`live.vix9d_vix_max`).
 * - **`entry-context`**, **`regime-split`**, **`iv-premium`**: the vol term structure and implied
 *   variance recorded at entry (honesty rules 11-12), the VIX9D/VIX gate's cut of each arm's closed
 *   results, and implied at entry against realised to expiry. All three are the module's derivations
 *   (the gate side never assigns an unmeasured row; a window with a hole is refused, not estimated).
 *
 * Every verb opens the ledgers read-only (`cli._open_ro`). Memoised per verb: these move when a
 * position settles, not on a poll.
 */

const UNAVAILABLE = "bwb evidence unavailable — the bwb package must be installed (pip install -e packages/bwb)";

export type BwbEvidenceVerb = "live-gap" | "entry-context" | "regime-split" | "iv-premium";

function argv(config: ConsoleConfig, verb: BwbEvidenceVerb): string[] {
  const head = ["-m", "cherrypick.bwb.cli", "--db", path.join(config.paths.bwbDir, "paper_trades.db"), verb];
  if (verb === "live-gap") return [...head, "--live-db", path.join(config.paths.bwbDir, "live_trades.db")];
  if (verb === "iv-premium") return [...head, "--windows"];
  return head;
}

function spawnVerb(config: ConsoleConfig, verb: BwbEvidenceVerb): Promise<SpawnedJson> {
  return spawnModuleCliAsync(argv(config, verb), UNAVAILABLE);
}

type Caller = (config: ConsoleConfig, verb: BwbEvidenceVerb) => SpawnedJson | Promise<SpawnedJson>;
let caller: Caller = spawnVerb;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setBwbEvidenceCaller(fn?: Caller): void {
  caller = fn ?? spawnVerb;
}

const TTL_MS = 120_000;
const cache = new AsyncMemo<SpawnedJson>();

/** Drop the memoised responses. Tests only. */
export function resetBwbEvidenceCache(): void {
  cache.clear();
}

async function read<T>(config: ConsoleConfig, verb: BwbEvidenceVerb, now: number): Promise<BridgedResult<T>> {
  const res = await cache.get(`${config.paths.bwbDir}|${verb}`, TTL_MS, now, () => caller(config, verb));
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error ?? UNAVAILABLE };
  if (res.json["ok"] !== true) {
    const err = res.json["error"];
    return { ok: false, data: null, error: typeof err === "string" ? err : `${UNAVAILABLE} — refused` };
  }
  return { ok: true, data: res.json as unknown as T, error: null };
}

export function readBwbLiveGap(config: ConsoleConfig, now = Date.now()): Promise<BridgedResult<BwbLiveGap>> {
  return read<BwbLiveGap>(config, "live-gap", now);
}

export async function readBwbVol(config: ConsoleConfig, now = Date.now()): Promise<BwbVolEvidence> {
  const [entryContext, regimeSplit, ivPremium] = await Promise.all([
    read<BwbEntryContext>(config, "entry-context", now),
    read<BwbRegimeSplit>(config, "regime-split", now),
    read<BwbIvPremium>(config, "iv-premium", now),
  ]);
  return { entryContext, regimeSplit, ivPremium };
}
