import fs from "node:fs";
import path from "node:path";
import type {
  RegimeBook,
  RegimeBreak,
  RegimeCell,
  RegimeCrossCell,
  RegimeCrossTab,
  RegimeCuts,
  RegimeCutsModule,
  RegimeCutsPayload,
  RegimeDimension,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, obj, readOnlyDb, str } from "./db.js";

/**
 * The regime-cuts artifact (2026-09-19), one reader for every module that writes one. The
 * module computes the cut (flies: `python run.py regime-cuts --write`; meic:
 * `python -m cherrypick.meic.regime_cuts --write`, both nightly under the supervisor) into
 * `data/<module>/regime_cuts.json`, plus a dated copy per session. This file shapes that JSON
 * into the shared type and derives exactly one thing: the union of dimension keys across books,
 * so the slide can lay out columns. Nothing else -- not a total, not a rate, not the `thin`
 * threshold, which is the writer's and rides on every cell.
 *
 * Absent, malformed and stale are three different facts and come back as three different
 * shapes. The earnings JSON reader collapses the first two into one empty result; that is the
 * wrong contract for a page whose whole point is saying what the evidence is.
 */

const SUPPORTED_CUT_VERSION = 1;
const LATEST = "regime_cuts.json";
const DATED = /^regime_cuts-(\d{4}-\d{2}-\d{2})\.json$/;

/** The ledger table whose newest session says whether the artifact is behind. Declared, not probed. */
const SESSION_TABLE: Record<RegimeCutsModule, string> = { flies: "fly_positions", meic: "ic_trades" };

function moduleDir(config: ConsoleConfig, module: RegimeCutsModule): string {
  return module === "flies" ? config.paths.fliesDir : config.paths.meicDir;
}

export function listRegimeCutSessions(config: ConsoleConfig, module: RegimeCutsModule): string[] {
  let names: string[];
  try {
    names = fs.readdirSync(moduleDir(config, module));
  } catch {
    return [];
  }
  return names
    .map((n) => DATED.exec(n)?.[1])
    .filter((s): s is string => s !== undefined)
    .sort();
}

function brk(v: unknown): RegimeBreak | null {
  const o = obj(v);
  const date = str(o["break_date"]);
  if (date === null) return null;
  return { breakDate: date, scope: str(o["scope"]) ?? "*", kind: str(o["kind"]) ?? "", reason: str(o["reason"]) };
}

function cell(v: unknown): RegimeCell {
  const o = obj(v);
  return {
    bucket: str(o["bucket"]) ?? "untagged",
    valueMin: num(o["value_min"]),
    valueMax: num(o["value_max"]),
    sessions: num(o["sessions"]) ?? 0,
    trades: num(o["trades"]) ?? 0,
    netPnl: num(o["net_pnl"]),
    avgPnl: num(o["avg_pnl"]),
    winRate: num(o["win_rate"]),
    profitFactor: num(o["profit_factor"]),
    completed: num(o["completed"]),
    completionRate: num(o["completion_rate"]),
    // The writer's flag, verbatim. A renderer that re-derived `sessions < 3` here would be a
    // second implementation of the threshold, and the two would drift.
    thin: o["thin"] === true,
  };
}

function dimension(v: unknown): RegimeDimension {
  const o = obj(v);
  return {
    coveragePct: num(o["coverage_pct"]),
    tagged: num(o["tagged"]) ?? 0,
    untagged: num(o["untagged"]) ?? 0,
    sessions: num(o["sessions"]) ?? 0,
    effectiveN: num(o["effective_n"]) ?? 0,
    dailyScale: o["daily_scale"] === true,
    degenerate: o["degenerate"] === true,
    underpowered: o["underpowered"] === true,
    buckets: Array.isArray(o["buckets"]) ? o["buckets"].map(cell) : [],
  };
}

function book(v: unknown): RegimeBook {
  const o = obj(v);
  const dims = obj(o["dimensions"]);
  const dimensions: Record<string, RegimeDimension> = {};
  for (const [k, d] of Object.entries(dims)) dimensions[k] = dimension(d);
  return {
    book: str(o["book"]) ?? "?",
    eraStart: str(o["era_start"]),
    eraBreak: brk(o["era_break"]),
    sessions: num(o["sessions"]) ?? 0,
    trades: num(o["trades"]) ?? 0,
    netPnl: num(o["net_pnl"]),
    winRate: num(o["win_rate"]),
    completed: num(o["completed"]),
    completionRate: num(o["completion_rate"]),
    dimensions,
  };
}

function crossCell(v: unknown): RegimeCrossCell {
  const o = obj(v);
  return {
    buckets: Array.isArray(o["buckets"]) ? o["buckets"].map(String) : [],
    sessions: num(o["sessions"]) ?? 0,
    trades: num(o["trades"]) ?? 0,
    netPnl: num(o["net_pnl"]),
    avgPnl: num(o["avg_pnl"]),
    winRate: num(o["win_rate"]),
    completed: num(o["completed"]),
    completionRate: num(o["completion_rate"]),
    thin: o["thin"] === true,
  };
}

function crossTab(v: unknown): RegimeCrossTab {
  const o = obj(v);
  return {
    dims: Array.isArray(o["dims"]) ? o["dims"].map(String) : [],
    books: Array.isArray(o["books"])
      ? o["books"].map((b) => {
          const bo = obj(b);
          return { book: str(bo["book"]) ?? "?", cells: Array.isArray(bo["cells"]) ? bo["cells"].map(crossCell) : [] };
        })
      : [],
  };
}

const EMPTY_BREAK: RegimeBreak = { breakDate: "", scope: "*", kind: "", reason: null };

export function shapeRegimeCuts(raw: Record<string, unknown>): RegimeCuts {
  const era = obj(raw["era"]);
  const books = Array.isArray(raw["books"]) ? raw["books"].map(book) : [];
  const dimensions: string[] = [];
  for (const b of books) for (const k of Object.keys(b.dimensions)) if (!dimensions.includes(k)) dimensions.push(k);
  const breaks = Array.isArray(era["breaks"]) ? era["breaks"] : [];
  const keep = (v: unknown): v is RegimeBreak => v !== null;
  return {
    cutVersion: num(raw["cut_version"]) ?? 0,
    module: str(raw["module"]) ?? "?",
    generatedAt: str(raw["generated_at"]),
    session: str(raw["session"]),
    symbol: str(raw["symbol"]),
    bookColumn: str(raw["book_column"]),
    entryModes: Array.isArray(raw["entry_modes"]) ? raw["entry_modes"].map(String) : null,
    thinBelowSessions: num(raw["thin_below_sessions"]) ?? 3,
    minEffectiveN: num(raw["min_effective_n"]) ?? 14,
    era: {
      start: str(era["start"]),
      key: str(era["key"]),
      boundingBreak: brk(era["bounding_break"]),
      breaks: breaks.map((b) => ({ ...(brk(b) ?? EMPTY_BREAK), binding: obj(b)["binding"] === true })),
      ignoredFuture: Array.isArray(era["ignored_future"]) ? era["ignored_future"].map(brk).filter(keep) : [],
      caveats: Array.isArray(era["caveats"]) ? era["caveats"].map(brk).filter(keep) : [],
    },
    books,
    crossTabs: Array.isArray(raw["cross_tabs"]) ? raw["cross_tabs"].map(crossTab) : [],
    dimensions,
  };
}

/** The newest resolved session in the module's paper ledger, or null when that cannot be told. */
function latestLedgerSession(config: ConsoleConfig, module: RegimeCutsModule): string | null {
  const out = readOnlyDb(path.join(moduleDir(config, module), "paper_trades.db"), (db) => {
    const row = db.prepare(`SELECT MAX(trade_date) AS d FROM ${SESSION_TABLE[module]}`).get() as { d: string | null };
    return row.d;
  });
  return out.status === "ok" ? out.value : null;
}

export function readRegimeCuts(
  config: ConsoleConfig,
  module: RegimeCutsModule,
  session?: string | null,
): RegimeCutsPayload {
  const dir = moduleDir(config, module);
  const file = path.join(dir, session ? `regime_cuts-${session}.json` : LATEST);
  const sessions = listRegimeCutSessions(config, module);
  // Absent before the try, as readOnlyDb does: a module that has never written one is not a failure.
  if (!fs.existsSync(file)) return { status: "absent", path: file, sessions };
  let raw: unknown;
  try {
    raw = JSON.parse(fs.readFileSync(file, "utf-8"));
  } catch (err) {
    return { status: "failed", error: `unparseable: ${err instanceof Error ? err.message : String(err)}`, sessions };
  }
  const o = obj(raw);
  const version = num(o["cut_version"]);
  if (version !== SUPPORTED_CUT_VERSION || !Array.isArray(o["books"])) {
    return {
      status: "failed",
      error: `unexpected shape (cut_version ${String(version)}; this console reads ${SUPPORTED_CUT_VERSION})`,
      sessions,
    };
  }
  const cuts = shapeRegimeCuts(o);
  const latest = latestLedgerSession(config, module);
  const stale =
    latest !== null && cuts.session !== null && latest > cuts.session
      ? { artifactSession: cuts.session, latestSession: latest }
      : null;
  return { status: "ok", cuts, stale, sessions };
}
