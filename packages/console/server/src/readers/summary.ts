import path from "node:path";
import {
  type TradeSummaryPayload,
  type TradeSummaryReturn,
  type TradeSummarySlice,
  type TradingMode,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { suiteEra } from "./db.js";
import { MODULE_DIR_KEY } from "./performance.js";
import { getFeatures, moduleOn } from "../services/featuresBridge.js";
import { CURRENT_ERA as PMCC_CURRENT_ERA } from "./pmcc.js";
import { readModuleSummary, resetSummaryCache, type SummarySlice } from "../services/summaryBridge.js";

/**
 * The Reports summary page's one read (`/api/reports/summary`, "The Reports summary's one read").
 *
 * The MEIC summary's shape generalised over every trading module — the prototype slide's cards,
 * fed by the module that owns the math (`python -m cherrypick.core.metrics summary`, through
 * `services/summaryBridge.ts`). This reader composes: it maps the page's filters onto each
 * module's ledger, converts the bridge's snake_case slices into the shared camelCase shape, and
 * returns one payload per read. Every figure arrives computed; nothing is re-derived here.
 *
 * The filter semantics, each with its own honest edge:
 * - `mode` reuses `performance.ts`'s `ledgerFile` registry — a module with no live book refuses
 *   VISIBLY (its slice carries the error; the card head names it) rather than showing paper
 *   under a live badge. bwb has a live ledger (its narrow pilot); it joins the live read when
 *   its pilot has settled sessions — the refusal message stays true until then.
 * - the suite epoch bounds every schema through `--start` (the lever `calibrate` enforces);
 *   pmcc's OWN stamped era scope rides `--era` (`readers/pmcc.ts` CURRENT_ERA, the module's
 *   era-scoping rule), and the CLI refuses an era flag on any schema that carries no stamp.
 * - `session` anchors the window in each module's OWN session stamps — flies settles intraday,
 *   calendars rides to the weekend — so the same ISO day names each module's own boundary; the
 *   day select is driven by the union of the in-scope slices' facets.
 * - `symbol`/`arm` narrow the slices and never the facets (the CLI's own rule), so a select
 *   never removes the option that would undo it.
 * - `module=all` (requested null) is a COMPARISON, never a pool: every module's slice rides in
 *   the payload and the page renders one comparison card per module — pooling arms across
 *   modules is a meaningless denominator.
 */

/** The ledger schema per summary module — the SHARED registry (`PERFORMANCE_MODULE_SCHEMA`),
 *  extended here with contango (which the performance slide predates and does not carry). One
 *  extension point, named, so the two stay comparable and a new module edits one line. */
export const SUMMARY_SCHEMA = {
  meic: "meic_ic",
  flies: "fly_book",
  earnings: "earnings",
  calendars: "dc_week",
  pmcc: "pmcc",
  curve: "curve_vx",
  contango: "contango_etf",
  bwb: "bwb_132",
} as const satisfies Record<string, string>;

/** Every module with a ledger schema, in the suite's trading order — derived from the registry
 *  above (one declaration; the web's `moduleOrder.ts` holds the display order and cannot be
 *  imported server-side). */
export type SummaryModuleId = keyof typeof SUMMARY_SCHEMA;
export const SUMMARY_MODULES = Object.keys(SUMMARY_SCHEMA) as SummaryModuleId[];

/** Per-module ledger directory — `performance.ts`'s map extended with contango, the same named
 *  extension point as the schema registry above. */
const SUMMARY_DIR_KEY = {
  ...MODULE_DIR_KEY,
  contango: "contangoDir",
} as const satisfies Record<SummaryModuleId, keyof ConsoleConfig["paths"]>;

/** The live-ledger file per summary module — `performance.ts`'s `LIVE_LEDGER` registry (meic,
 *  flies, earnings), extended with bwb (its narrow live pilot shares the paper schema) and
 *  contango (whose page carries its own live stance). A module absent here refuses a live read
 *  VISIBLY rather than showing paper under a live badge. */
export const SUMMARY_LIVE_LEDGER = {
  meic: "meic_trades.db",
  flies: "live_trades.db",
  earnings: "earnings_trades.db",
  bwb: "live_trades.db",
  contango: "live_trades.db",
} as const satisfies Partial<Record<SummaryModuleId, string>>;

/** `capital` is `ledgers`' per-row capital at risk — a defined max loss for the option
 *  structures, cash deployed for contango's shares, ABSENT for flies (its ledger carries no
 *  per-trade capital: a legged book's risk depends on completion, per `ledgers`' own comment).
 *  The ratio's label follows the MODULE — "return on risk" where capital is a max loss, "on
 *  deployed" where it is cash, and "n/r" where the rows cannot support one — exactly review's
 *  own distinction between `on_max_risk` and `on_deployed`. */
export const ROR_LABEL: Record<SummaryModuleId, { capital: string; note: string }> = {
  meic: { capital: "capital at risk", note: "the condor's own worst case at expiry" },
  flies: { capital: "capital at risk", note: "no per-trade capital — a legged book's risk depends on completion" },
  earnings: { capital: "capital at risk", note: "recorded at entry" },
  calendars: { capital: "capital at risk", note: "the debit paid — the structure's defined max loss" },
  pmcc: { capital: "capital at risk", note: "the net debit — the structure's defined max loss" },
  curve: { capital: "capital at risk", note: "the spread's max loss" },
  contango: { capital: "cash deployed", note: "cash deployed in shares" },
  bwb: { capital: "capital at risk", note: "the entry max loss — the structure's defined worst case" },
};

const RANGES = ["day", "week", "month", "year", "all"] as const;
export type SummaryRange = (typeof RANGES)[number];

function toNum(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function pctClamped(v: number | null): number | null {
  return v === null ? null : Math.max(-100, Math.min(100, v));
}

/** One bridge slice into the shared shape — key renames and the two-form ratio only. Nothing
 *  derived: `value` arrives computed by the CLI, the clamp is presentation arithmetic. */
function toShared(m: SummaryModuleId, mode: TradingMode, s: SummarySlice): TradeSummarySlice {
  const ror: TradeSummaryReturn = {
    capitalAtRisk: toNum((s.hero as unknown as Record<string, unknown>)["capital"]),
    returnOnRiskPct: pctClamped(s.return?.value ?? null),
    returnOnRiskRawPct: s.return?.value ?? null,
    coverage: s.return?.coverage ?? "0/0",
  };
  return {
    module: m,
    mode,
    schema: s.schema,
    window: { from: s.window?.from ?? null, to: s.window?.to ?? null },
    hero: {
      trades: s.hero?.trades ?? 0,
      sessions: s.hero?.sessions ?? 0,
      gross: s.hero?.gross ?? 0,
      fees: s.hero?.fees ?? 0,
      net: s.hero?.net ?? 0,
      wins: s.hero?.wins ?? 0,
      winPct: toNum(s.hero?.win_pct),
    },
    return: ror,
    returnLabel: ROR_LABEL[m],
    byArm: Object.entries(s.by_arm ?? {}).map(([arm, a]) => ({
      arm,
      trades: a.trades,
      sessions: a.sessions,
      net: a.net,
      gross: a.gross,
      fees: a.fees,
      wins: a.wins,
      winPct: a.win_pct,
      capitalCoverage: a.capital_coverage,
      return: a.return,
    })),
    daily: (s.daily ?? []).map((d) => ({ session: d.session, net: d.net, trades: d.trades, wins: d.wins })),
    capture: s.capture ?? null,
    facets: s.facets ?? { sessions: [], symbols: [], arms: [] },
  };
}

/** The per-module denominator vocabulary, riding the payload — the page renders the label the
 *  reader chose and keeps no module table of its own. */
export const RETURN_LABELS: Record<string, { capital: string; note: string }> =
  Object.fromEntries(SUMMARY_MODULES.map((m) => [m, ROR_LABEL[m]]));

/** One failed module's slice: the shared shape's every key present, nothing zero, the error at
 *  the top. One absent db degrades ITS slice (an unreadable card head) and never the page. */
function absentSlice(m: SummaryModuleId, mode: TradingMode, schema: string, error?: string): TradeSummarySlice {
  const base = toShared(m, mode, {
    ok: false,
    schema,
    window: { from: null, to: null },
    hero: { trades: 0, sessions: 0, net: 0, gross: 0, fees: 0, wins: 0, win_pct: null, capital: null, capital_coverage: "0/0" },
    return: { value: null, coverage: "0/0" },
    by_arm: {},
    daily: [],
    facets: { sessions: [], symbols: [], arms: [] },
    capture: { value: null, n: 0 },
  } as unknown as SummarySlice);
  return error === undefined ? base : { ...base, error };
}

export interface SummaryFilter {
  module: string | null;
  session: string | null;
  symbol: string | null;
  arm: string | null;
  range: SummaryRange;
  mode: TradingMode;
}

export function parseSummaryFilter(q: Record<string, unknown>): SummaryFilter {
  const rawModule = typeof q["module"] === "string" && q["module"] !== "" ? q["module"] : null;
  const rawRange = typeof q["range"] === "string" ? q["range"] : null;
  const rawMode = typeof q["mode"] === "string" ? q["mode"] : null;
  const rawSession = typeof q["session"] === "string" && /^\d{4}-\d{2}-\d{2}$/.test(q["session"]) ? q["session"] : null;
  return {
    module: rawModule && (SUMMARY_MODULES as readonly string[]).includes(rawModule) ? rawModule : null,
    session: rawSession,
    symbol: typeof q["symbol"] === "string" && q["symbol"] !== "" ? q["symbol"] : null,
    arm: typeof q["arm"] === "string" && q["arm"] !== "" ? q["arm"] : null,
    range: (RANGES as readonly string[]).includes(rawRange ?? "") ? (rawRange as SummaryRange) : "day",
    mode: rawMode === "live" ? "live" : "paper",
  };
}

/** The switchboard's verdict per module — asked of the ORCHESTRATOR (`configcli` op `features`,
 *  through the shared `getFeatures` bridge), never read off the config file here: "is earnings
 *  off?" folds the user's switch together with the machine's capabilities, that rule lives in
 *  Python, and a TypeScript copy reading the file directly would be a second answer free to drift
 *  from the one the scheduler acts on (the console's own documented rule). "Unknown is visible":
 *  a failed bridge leaves every module in the comparison — a broken bridge costs a chip, not
 *  data. The features bridge is memoised 15s and dropped on every config save, so a module
 *  switched off in the browser leaves this page on the next read. */
function hiddenModules(): SummaryModuleId[] {
  const features = getFeatures();
  return SUMMARY_MODULES.filter((m) => !moduleOn(features, m));
}

export function readTradeSummary(config: ConsoleConfig, f: SummaryFilter): TradeSummaryPayload {
  const suite = suiteEra(config.paths.orchestratorConfig);
  const requested = f.module;
  // The switchboard decides what the comparison shows: a module the suite turned off is not part
  // of the suite's day, so it holds no comparison card. It stays reachable by a DIRECT ask
  // (?module=pmcc) — a reader naming a module wants THAT module — but the slice then states the
  // refusal instead of numbers, so an off module can never quietly masquerade as a silent empty
  // book.
  const hidden = hiddenModules();
  const comparison = [...SUMMARY_MODULES].filter((m) => !hidden.includes(m));
  const modules: SummaryModuleId[] = requested && (SUMMARY_MODULES as string[]).includes(requested)
    ? [requested as SummaryModuleId]
    : comparison;
  const slices: TradeSummarySlice[] = [];
  const moduleErrors: Record<string, string> = {};
  const modulesRead: string[] = [];

  for (const m of modules) {
    const schema = SUMMARY_SCHEMA[m];
    // A directly-asked module the switchboard turned off states THAT refusal — before any
    // store probe, so "no store" can never mask "the operator switched this off". The
    // question "why is this module not trading" is answered by the config, not the ledger.
    if (hidden.includes(m)) {
      moduleErrors[m] = `${m} is switched off in the suite config (modules.${m}.enabled) — no ledger has been kept`;
      slices.push(absentSlice(m, f.mode, schema, moduleErrors[m]));
      continue;
    }
    const file = f.mode === "paper" ? "paper_trades.db" : (SUMMARY_LIVE_LEDGER as Partial<Record<SummaryModuleId, string>>)[m] ?? null;
    if (file === null) {
      moduleErrors[m] = `${m} has no live ledger — its reading is paper only`;
      slices.push(absentSlice(m, f.mode, schema, moduleErrors[m]));
      continue;
    }
    const dbPath = path.join(config.paths[SUMMARY_DIR_KEY[m]], file);
    const raw = readModuleSummary({
      dbPath,
      schema,
      session: f.session,
      symbol: f.symbol,
      arm: f.arm,
      width: f.range,
      // The suite epoch's bound for every schema; pmcc also scopes to its OWN stamped era.
      start: suite.from,
      end: null,
      era: m === "pmcc" ? PMCC_CURRENT_ERA : null,
    });
    if (raw.ok === false && raw.error) moduleErrors[m] = raw.error;
    else modulesRead.push(m);
    // The bridge's error rides ONTO the slice (absent store, spawn failure, CLI refusal) — a
    // failed read is an unreadable card head, never a silent empty one. The no-live-ledger
    // branch above builds its own absent slice; every other failure keeps the slice it got.
    slices.push(raw.error ? { ...toShared(m, f.mode, raw), error: raw.error } : toShared(m, f.mode, raw));
  }

  return {
    requested,
    mode: f.mode,
    range: f.range,
    session: f.session,
    symbol: f.symbol,
    arm: f.arm,
    era: { key: "suite", from: suite.from },
    slices,
    modulesRead,
    moduleErrors,
    // What the switchboard hid from the comparison, so the page can say "4 modules off" in one
    // muted line and offer the direct ask, instead of four dead cards or a silent absence.
    modulesHidden: hidden,
    returnLabels: RETURN_LABELS,
  };
}

export function resetSummaryReads(): void {
  resetSummaryCache();
}
