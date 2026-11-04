import type {
  BacktestOverview,
  BacktestRunReport,
  BacktestTradeLog,
  BacktestTradePath,
  BridgedResult,
  StudyArmFigures,
  StudyRunFigures,
  StudyRunSummaries,
} from "@console/shared";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * The Backtest page's two reads of `packages/backtest` (`python -m cherrypick.backtest read`):
 *
 * - `read overview`: the licence clock, the vendor stores, the vendor-against-recorder validation
 *   per cadence, the run list and the trial counts.
 * - `read run --id <run_id> [--capital <dollars>] [--risk-pct <percent>]`: one run's report, with its
 *   cards (`cards.py`) attached as `cards`, on the chosen account or the run's own peak risk, the
 *   compounded lens sized at the chosen risk per session (`--risk-unit`: max loss or stop distance),
 *   and with a chosen capital the sizing check at its drawdown cap (`--dd-pct`). An unknown id is
 *   the module's own `{"ok": false, "error"}` on stdout with exit 1, which is passed on as that error.
 * - `read trades --id <run_id> --arm <arm>`: one arm's trade log (`cards.trade_log`), asked for only
 *   when the trade log is opened, so the run report stays light.
 * - `read path --id --arm --session --position`: one MEIC condor's marks through its session
 *   (`tradepath.py`), asked for when a trade in the log is opened.
 * - `read summaries --ids A,B`: each run's arms in brief (the report's mean, sessions and trades,
 *   and a thinned running net), for the studies tiles: one read for every tile on the board.
 *
 * All are read-only on the Python side and neither contacts the vendor. Every verdict on the page
 * (the unlock, each check, the paired interval) is the module's; nothing here recomputes one.
 *
 * Async (`spawnModuleCliAsync`, the sync spawner's parse): the overview opens every store, and a
 * synchronous spawn would hold the whole server for it. Memoised ~30 s; a second request for a read
 * in flight waits on the same subprocess.
 *
 * The caller is NOT the gate. Both routes (`routes/backtest.ts`) refuse before reaching here unless
 * the orchestrator's features answer says `backtest: true`; this module never checks it.
 */

const UNAVAILABLE = "backtest read unavailable — cherrypick-backtest must be installed";

export type BacktestRequest =
  | { kind: "overview" }
  | { kind: "run"; id: string; capital?: number; riskPct?: number; riskUnit?: string; ddPct?: number }
  | { kind: "trades"; id: string; arm: string }
  | { kind: "path"; id: string; arm: string; session: string; position: string }
  | { kind: "summaries"; ids: string[] };

function spawnRequest(req: BacktestRequest): Promise<SpawnedJson> {
  if (req.kind === "overview") {
    return spawnModuleCliAsync(["-m", "cherrypick.backtest", "read", "overview"], UNAVAILABLE);
  }
  if (req.kind === "trades") {
    // Exit 1 is the module's refusal (no such run or arm), read as an answer.
    return spawnModuleCliAsync(["-m", "cherrypick.backtest", "read", "trades", "--id", req.id, "--arm", req.arm], UNAVAILABLE, [0, 1]);
  }
  if (req.kind === "summaries") {
    return spawnModuleCliAsync(["-m", "cherrypick.backtest", "read", "summaries", "--ids", req.ids.join(",")], UNAVAILABLE);
  }
  if (req.kind === "path") {
    const args = ["-m", "cherrypick.backtest", "read", "path", "--id", req.id, "--arm", req.arm];
    args.push("--session", req.session, "--position", req.position);
    return spawnModuleCliAsync(args, UNAVAILABLE, [0, 1]);
  }
  const args = ["-m", "cherrypick.backtest", "read", "run", "--id", req.id];
  if (req.capital !== undefined) args.push("--capital", String(req.capital));
  if (req.riskPct !== undefined) args.push("--risk-pct", String(req.riskPct));
  if (req.riskUnit !== undefined) args.push("--risk-unit", req.riskUnit);
  if (req.ddPct !== undefined) args.push("--dd-pct", String(req.ddPct));
  // Exit 1 carries the module's own refusal on stdout ("no run ..."): read it as an answer.
  return spawnModuleCliAsync(args, UNAVAILABLE, [0, 1]);
}

let caller: (req: BacktestRequest) => Promise<SpawnedJson> = spawnRequest;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setBacktestCaller(fn?: (req: BacktestRequest) => Promise<SpawnedJson>): void {
  caller = fn ?? spawnRequest;
}

export const TTL_MS = 30_000;
const cache = new Map<string, { at: number; value: Promise<SpawnedJson> }>();

/** Drop the memoised responses. Tests only. */
export function resetBacktestCache(): void {
  cache.clear();
}

function memo(req: BacktestRequest, now: number): Promise<SpawnedJson> {
  const key =
    req.kind === "overview"
      ? "overview"
      : req.kind === "trades"
        ? `trades|${req.id}|${req.arm}`
        : req.kind === "path"
          ? `path|${req.id}|${req.arm}|${req.session}|${req.position}`
          : req.kind === "summaries"
            ? `summaries|${req.ids.join(",")}`
            : `run|${req.id}|${String(req.capital ?? "")}|${String(req.riskPct ?? "")}|${req.riskUnit ?? ""}|${String(req.ddPct ?? "")}`;
  const hit = cache.get(key);
  if (hit !== undefined && now - hit.at < TTL_MS) return hit.value;
  // Expired entries go when anything is read, so the map holds at most a window's worth of ids.
  for (const [k, v] of cache) if (now - v.at >= TTL_MS) cache.delete(k);
  const value = caller(req);
  cache.set(key, { at: now, value });
  return value;
}

function shape<T>(res: SpawnedJson, pick: (json: Record<string, unknown>) => unknown): BridgedResult<T> {
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error ?? UNAVAILABLE };
  if (res.json["ok"] !== true) return { ok: false, data: null, error: String(res.json["error"] ?? UNAVAILABLE) };
  const data = pick(res.json);
  if (data === null || typeof data !== "object") return { ok: false, data: null, error: `${UNAVAILABLE} — malformed reply` };
  return { ok: true, data: data as T, error: null };
}

export async function readBacktestOverview(now = Date.now()): Promise<BridgedResult<BacktestOverview>> {
  return shape<BacktestOverview>(await memo({ kind: "overview" }, now), (j) => j);
}

/** The caller has already checked `id` against `BACKTEST_RUN_ID`, `capital` against
 *  `BACKTEST_CAPITAL` and `riskPct` against `BACKTEST_RISK_PCT`. */
export async function readBacktestRun(
  id: string,
  capital?: number,
  riskPct?: number,
  riskUnit?: string,
  ddPct?: number,
  now = Date.now(),
): Promise<BridgedResult<BacktestRunReport>> {
  const req: BacktestRequest = {
    kind: "run",
    id,
    ...(capital === undefined ? {} : { capital }),
    ...(riskPct === undefined ? {} : { riskPct }),
    ...(riskUnit === undefined ? {} : { riskUnit }),
    ...(ddPct === undefined ? {} : { ddPct }),
  };
  return shape<BacktestRunReport>(await memo(req, now), (j) => {
    const report = j["report"];
    if (report === null || typeof report !== "object") return null;
    return j["cards"] === undefined ? report : { ...report, cards: j["cards"] };
  });
}

/** One arm's trade log. The caller has already checked `id` and `arm` against their shapes. */
export async function readBacktestTrades(id: string, arm: string, now = Date.now()): Promise<BridgedResult<BacktestTradeLog>> {
  return shape<BacktestTradeLog>(await memo({ kind: "trades", id, arm }, now), (j) =>
    Array.isArray(j["trades"]) ? { run_id: j["run_id"], arm: j["arm"], trades: j["trades"] } : null,
  );
}

/** One trade's path. The caller has already checked every argument against its shape. */
export async function readBacktestPath(
  id: string,
  arm: string,
  session: string,
  position: string,
  now = Date.now(),
): Promise<BridgedResult<BacktestTradePath>> {
  return shape<BacktestTradePath>(await memo({ kind: "path", id, arm, session, position }, now), (j) => {
    if (!Array.isArray(j["points"])) return null;
    const { ok: _ok, ...rest } = j;
    return rest;
  });
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

/** One run in brief as the package wrote it; a path keeps only its finite numbers. */
function toRunFigures(v: unknown): StudyRunFigures {
  const r = rec(v);
  const arms: Record<string, StudyArmFigures> = {};
  for (const [arm, a] of Object.entries(rec(r["arms"]))) {
    const x = rec(a);
    const path = Array.isArray(x["path"]) ? x["path"].filter((p): p is number => typeof p === "number" && Number.isFinite(p)) : [];
    arms[arm] = { mean: num(x["mean"]), sessions: num(x["sessions"]), trades: num(x["trades"]), path };
  }
  return { module: str(r["module"]), window: str(r["window"]), provisional: r["provisional"] === true, arms };
}

/** Each run's arms in brief, for the studies tiles. The caller has already checked every id's
 *  shape and their count; they are sorted here so one set of tiles is one memoised read. */
export async function readRunSummaries(ids: string[], now = Date.now()): Promise<BridgedResult<StudyRunSummaries>> {
  const sorted = [...new Set(ids)].sort();
  if (sorted.length === 0) return { ok: true, data: { runs: {}, missing: [] }, error: null };
  return shape<StudyRunSummaries>(await memo({ kind: "summaries", ids: sorted }, now), (j) => {
    const runs: Record<string, StudyRunFigures> = {};
    for (const [id, v] of Object.entries(rec(j["runs"]))) runs[id] = toRunFigures(v);
    const missing = Array.isArray(j["missing"]) ? j["missing"].filter((x): x is string => typeof x === "string") : [];
    return { runs, missing };
  });
}
