/**
 * The Live page's one broker read: the orchestrator's `positions` verb, run as a subprocess and
 * memoised, never the SDK from here.
 *
 * Why a bridge and not a call: the console holds no order path and `server/test/dry-run-only.test.ts`
 * scans every source file for the SDK's order methods. A balance read is not an order, but the
 * masking of account numbers, the "a mid is not a fill" flags and the unpriced-leg accounting all
 * live in `packages/orchestrator/positions.py` and are tested there -- re-deriving them in
 * TypeScript would be a second copy free to drift (the console's own bridge-a-derivation rule).
 *
 * Memoised for `TTL_MS` because the page polls every 15s and this is a real broker round-trip; a
 * failure is memoised too (shorter), so a broker outage costs one call a minute, not one a poll.
 * Never fatal: the tile degrades to the local figure and says why.
 */
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { LiveAccount } from "@console/shared";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..", "..");
const RUN_PY = path.join(REPO_ROOT, "packages", "orchestrator", "run.py");

const TTL_MS = 60_000;
const FAIL_TTL_MS = 30_000;

export interface BrokerRead {
  ok: boolean;
  account: LiveAccount | null;
  error: string | null;
}
export type BrokerCaller = () => Promise<BrokerRead>;

let caller: BrokerCaller | null = null;
let memo: { at: number; result: BrokerRead } | null = null;
let inFlight: Promise<BrokerRead> | null = null;

/** Tests only: replace the subprocess with a fake, and reset the memo. */
export function setBrokerCaller(fn?: BrokerCaller): void {
  caller = fn ?? null;
  memo = null;
  inFlight = null;
}

function num(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function pick(balances: Record<string, unknown>, ...names: string[]): number | null {
  for (const [k, v] of Object.entries(balances)) {
    const key = k.toLowerCase().replace(/_/g, "-");
    if (names.some((n) => key === n)) return num(v);
  }
  return null;
}

function spawnPositions(): Promise<BrokerRead> {
  // Asynchronous on purpose: a positions read is a real broker round-trip (10-20s), and a
  // synchronous spawn here blocked the whole server for its duration -- the page's own HTML
  // could not be served while its tile was being fetched (found in the first browser check).
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn("python", [RUN_PY, "positions", "--json"], { windowsHide: true });
    } catch (err) {
      resolve({ ok: false, account: null, error: `positions bridge failed: ${(err as Error).message}` });
      return;
    }
    let out = "";
    let errText = "";
    const timer = setTimeout(() => child.kill(), 45_000);
    child.stdout.on("data", (d: Buffer) => (out += d.toString("utf-8")));
    child.stderr.on("data", (d: Buffer) => (errText += d.toString("utf-8")));
    child.on("error", (err) => {
      clearTimeout(timer);
      resolve({ ok: false, account: null, error: `positions bridge failed: ${err.message}` });
    });
    child.on("close", (status) => {
      clearTimeout(timer);
      const start = out.indexOf("{");
      if (status !== 0 || start < 0) {
        const detail = errText.trim().split(/\r?\n/).pop() ?? `exit ${String(status)}`;
        resolve({ ok: false, account: null, error: `positions bridge: ${detail || "no output"}` });
        return;
      }
      try {
        resolve(shape(JSON.parse(out.slice(start))));
      } catch {
        resolve({ ok: false, account: null, error: "positions bridge: unparseable output" });
      }
    });
  });
}

/** The designated account's balances and whole-account marks out of the verb's JSON. Exported for
 *  the test that pins the field names against the SDK's underscored keys. */
export function shape(parsed: unknown): BrokerRead {
  const doc = (parsed ?? {}) as Record<string, unknown>;
  if (doc["ok"] !== true) return { ok: false, account: null, error: String(doc["error"] ?? "positions: not ok") };
  const accounts = Array.isArray(doc["accounts"]) ? (doc["accounts"] as Array<Record<string, unknown>>) : [];
  const acct = accounts.find((a) => a["designated"] === true) ?? accounts[0];
  if (acct === undefined) return { ok: false, account: null, error: "positions: no account" };
  const balances = (acct["balances"] ?? {}) as Record<string, unknown>;
  return {
    ok: true,
    error: null,
    account: {
      account: String(acct["account"] ?? "****"),
      netLiquidatingValue: pick(balances, "net-liquidating-value"),
      derivativeBuyingPower: pick(balances, "derivative-buying-power"),
      usedDerivativeBuyingPower: pick(balances, "used-derivative-buying-power"),
      value: num(acct["value"]),
      openPl: num(acct["open_pl"]),
      dayPl: num(acct["day_pl"]),
      legCount: num(acct["leg_count"]),
      unpricedCount: num(acct["unpriced_count"]),
      at: String(doc["generated_at"] ?? new Date().toISOString()),
    },
  };
}

/**
 * Non-blocking on purpose. The page's payload must never wait on a broker round-trip (the first
 * browser check caught the whole page sitting on skeletons for the 10-20s the positions verb
 * takes): this returns whatever the memo holds -- fresh, or stale with a refresh already in
 * flight -- and starts a refresh when one is due. A cold start answers "fetching" once and the
 * next poll (15s) has the account. `FETCHING` is the one error string the page reads as
 * "coming", not "failed".
 */
export const FETCHING = "fetching the account from the broker";

export function readBrokerAccount(): BrokerRead {
  const now = Date.now();
  const fresh = memo !== null && now - memo.at < (memo.result.ok ? TTL_MS : FAIL_TTL_MS);
  if (!fresh && inFlight === null) {
    inFlight = (caller ?? spawnPositions)().then((result) => {
      memo = { at: Date.now(), result };
      inFlight = null;
      return result;
    });
  }
  return memo?.result ?? { ok: false, account: null, error: FETCHING };
}

/** Tests only: settle whatever refresh is in flight. */
export async function settleBrokerRead(): Promise<void> {
  if (inFlight !== null) await inFlight;
}
