import path from "node:path";
import type {
  EnsembleEntryRow,
  EnsembleGate,
  EnsembleHistory,
  EnsembleHistoryRow,
  EnsembleLeg,
  EnsembleLine,
  EnsembleMark,
  EnsemblePayload,
  EnsemblePerformance,
  EnsemblePosition,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { rangeClauses, type DateRange } from "./dateRange.js";
import { hasTable, num, str, type DatabaseHandle, withReadOnlyDb } from "./db.js";
import { EMPTY_TRADE_TOTALS, tradeTotals } from "./positionCash.js";

/**
 * ensemble's read layer (packages/ensemble): research item 75's arms in paper, one store and no
 * `mode`. An arm is whatever name the ledger carries, so every list of arms below is taken from rows
 * (`SELECT DISTINCT arm`); nothing here names one. The money is the module's own -- each closed
 * structure already adds up (entry_value + exit_value = gross_pnl; gross_pnl - fees - settlement -
 * slippage = net_pnl) -- so rows are read, never re-derived.
 *
 * What this file does compute is the running sum a curve and a drawdown are made of, and it does so
 * exactly as the module's `analytics.py` does: closed positions with a net, in exit order (exit
 * session, then entry session, then arm), the drawdown the largest peak-to-trough fall of the running
 * net. The module's own `summary` verb is the reference for the numbers.
 */

const DB_FILE = "paper_trades.db";

function dbPath(config: ConsoleConfig): string {
  return path.join(config.paths.ensembleDir, DB_FILE);
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

function rows(db: DatabaseHandle, sql: string, ...params: string[]): Record<string, unknown>[] {
  return db.prepare<string[], Record<string, unknown>>(sql).all(...params);
}

// ---------------------------------------------------------------------------------------- legs

function legFromJson(raw: unknown): EnsembleLeg | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const strike = num(r["strike"]);
  const side = num(r["side"]);
  const right = str(r["right"]);
  if (strike === null || side === null || right === null) return null;
  return { role: str(r["name"]) ?? "", right, side, strike };
}

/** The legs recorded in the entry the engine produced: the fallback for a position with no leg rows. */
function entryLegs(entryJson: string | null): EnsembleLeg[] {
  if (entryJson === null) return [];
  try {
    const parsed = JSON.parse(entryJson) as { legs?: unknown };
    return Array.isArray(parsed.legs) ? parsed.legs.map(legFromJson).filter((l): l is EnsembleLeg => l !== null) : [];
  } catch {
    return [];
  }
}

function legsByPosition(db: DatabaseHandle): Map<string, EnsembleLeg[]> {
  const out = new Map<string, EnsembleLeg[]>();
  if (!hasTable(db, "ensemble_legs")) return out;
  for (const r of rows(db, "SELECT * FROM ensemble_legs ORDER BY id")) {
    const id = str(r["position_id"]) ?? "";
    const list = out.get(id) ?? [];
    list.push({ role: str(r["leg_role"]) ?? "", right: str(r["right"]) ?? "", side: num(r["side"]) ?? 0, strike: num(r["strike"]) ?? 0 });
    out.set(id, list);
  }
  return out;
}

// --------------------------------------------------------------------------------------- marks

/** The latest usable mark of each open position. */
function latestMarks(db: DatabaseHandle): Map<string, EnsembleMark> {
  const out = new Map<string, EnsembleMark>();
  if (!hasTable(db, "ensemble_marks")) return out;
  for (const r of rows(
    db,
    `SELECT m.* FROM ensemble_marks m
       JOIN (SELECT position_id, MAX(ts) AS ts FROM ensemble_marks WHERE usable = 1 GROUP BY position_id) last
         ON last.position_id = m.position_id AND last.ts = m.ts
      WHERE m.usable = 1`,
  )) {
    out.set(str(r["position_id"]) ?? "", { ts: str(r["ts"]) ?? "", value: num(r["mark_value"]), spot: num(r["spot"]) });
  }
  return out;
}

/** The newest spot any mark recorded, usable or not: a refused quote still carries one. */
function latestSpots(db: DatabaseHandle): Map<string, number> {
  const out = new Map<string, number>();
  if (!hasTable(db, "ensemble_marks")) return out;
  for (const r of rows(
    db,
    `SELECT m.position_id, m.spot FROM ensemble_marks m
       JOIN (SELECT position_id, MAX(ts) AS ts FROM ensemble_marks WHERE spot IS NOT NULL GROUP BY position_id) last
         ON last.position_id = m.position_id AND last.ts = m.ts`,
  )) {
    const spot = num(r["spot"]);
    if (spot !== null) out.set(str(r["position_id"]) ?? "", spot);
  }
  return out;
}

// ------------------------------------------------------------------------------- positions

function position(
  r: Record<string, unknown>,
  legs: Map<string, EnsembleLeg[]>,
  marks: Map<string, EnsembleMark>,
  spots: Map<string, number>,
): EnsemblePosition {
  const id = str(r["position_id"]) ?? "";
  const status = str(r["status"]) ?? "";
  const open = status === "open";
  const usable = open ? (marks.get(id) ?? null) : null;
  const spot = usable?.spot ?? (open ? (spots.get(id) ?? null) : null);
  const entryValue = num(r["entry_value"]) ?? 0;
  return {
    positionId: id,
    arm: str(r["arm"]) ?? "",
    symbol: str(r["symbol"]) ?? "",
    status,
    expiration: str(r["expiration"]) ?? "",
    entrySession: str(r["entry_session"]) ?? "",
    entryTime: str(r["entry_time"]),
    quantity: num(r["quantity"]) ?? 1,
    entrySpot: num(r["entry_spot"]),
    entryMid: num(r["entry_mid"]),
    capital: num(r["capital"]),
    entryValue,
    entryFees: num(r["entry_fees"]) ?? 0,
    entrySlippage: num(r["entry_slippage"]) ?? 0,
    exitReason: str(r["exit_reason"]),
    exitSession: str(r["exit_session"]),
    exitSpot: num(r["exit_spot"]),
    exitValue: num(r["exit_value"]),
    settlePrice: num(r["settle_price"]),
    grossPnl: num(r["gross_pnl"]),
    fees: num(r["fees"]),
    settlement: num(r["settlement"]),
    slippage: num(r["slippage"]),
    netPnl: num(r["net_pnl"]),
    netStress: num(r["net_stress"]),
    netMid: num(r["net_mid"]),
    legs: legs.get(id) ?? entryLegs(str(r["entry_json"])),
    mark: usable === null && spot === null ? null : { ts: usable?.ts ?? "", value: usable?.value ?? null, spot },
    unrealised: usable?.value == null ? null : round2(entryValue + usable.value),
  };
}

// --------------------------------------------------------------------------- the register

function parseDetail(text: string | null): { check: string | null; value: string | null } {
  if (text === null) return { check: null, value: null };
  try {
    const d = JSON.parse(text) as unknown;
    if (typeof d === "object" && d !== null && !Array.isArray(d)) {
      const o = d as Record<string, unknown>;
      const v = o["value"];
      return {
        check: typeof o["check"] === "string" ? o["check"] : null,
        value: v === undefined || v === null ? null : typeof v === "string" ? v : JSON.stringify(v),
      };
    }
  } catch {
    // Not JSON: shown as the value it is.
  }
  return { check: null, value: text };
}

/** How many ticks each arm's refusal reason has been given in a session, from the journal. */
function refusalCounts(db: DatabaseHandle, session: string): Map<string, { occurrences: number; detail: string | null }> {
  const out = new Map<string, { occurrences: number; detail: string | null }>();
  if (!hasTable(db, "ensemble_decisions")) return out;
  for (const r of rows(
    db,
    "SELECT * FROM ensemble_decisions WHERE trade_date = ? AND mode = 'entry' AND accepted = 0 ORDER BY last_ts, id",
    session,
  )) {
    out.set(`${str(r["arm"]) ?? ""}\u0000${str(r["reason"]) ?? ""}`, {
      occurrences: Math.round(num(r["occurrences"]) ?? 1),
      detail: str(r["detail"]),
    });
  }
  return out;
}

function entryRow(r: Record<string, unknown>, counts: Map<string, { occurrences: number; detail: string | null }>): EnsembleEntryRow {
  const arm = str(r["arm"]) ?? "";
  const refusal = str(r["refusal"]);
  const outcome = str(r["outcome"]) ?? "";
  const journal = refusal === null ? undefined : counts.get(`${arm}\u0000${refusal}`);
  // The register keeps the last reason's detail; the journal's row for that reason is the fallback.
  const detail = parseDetail(str(r["detail"]) ?? journal?.detail ?? null);
  const gate: EnsembleGate | null =
    refusal === null || outcome === "entered"
      ? null
      : { reason: refusal, check: detail.check, value: detail.value, occurrences: journal?.occurrences ?? null };
  return {
    arm,
    symbol: str(r["symbol"]) ?? "",
    entrySession: str(r["entry_session"]) ?? "",
    outcome,
    refusal,
    positionId: str(r["position_id"]),
    decidedAt: str(r["decided_at"]),
    gate,
  };
}

function sessionOf(db: DatabaseHandle): string | null {
  if (hasTable(db, "ensemble_loop_iterations")) {
    const d = db.prepare<[], { d: string | null }>("SELECT MAX(session_date) AS d FROM ensemble_loop_iterations").get()?.d;
    if (d != null) return d;
  }
  if (hasTable(db, "ensemble_entries")) {
    return db.prepare<[], { d: string | null }>("SELECT MAX(entry_session) AS d FROM ensemble_entries").get()?.d ?? null;
  }
  return null;
}

/** Every arm the ledger names, in any table that carries one. */
function armsOf(db: DatabaseHandle): string[] {
  const names = new Set<string>();
  for (const table of ["ensemble_positions", "ensemble_entries", "ensemble_decisions"]) {
    if (!hasTable(db, table)) continue;
    for (const r of rows(db, `SELECT DISTINCT arm FROM ${table}`)) {
      const a = str(r["arm"]);
      if (a !== null) names.add(a);
    }
  }
  return [...names].sort();
}

function emptyPayload(): EnsemblePayload {
  return { session: null, dbPresent: false, arms: [], positions: [], today: [], measurementBreaks: [], lastIteration: null };
}

export function readEnsemble(config: ConsoleConfig): EnsemblePayload {
  return withReadOnlyDb<EnsemblePayload>(dbPath(config), emptyPayload(), (db) => {
    const session = sessionOf(db);
    const legs = legsByPosition(db);
    const marks = latestMarks(db);
    const spots = latestSpots(db);
    const positions = hasTable(db, "ensemble_positions")
      ? rows(db, "SELECT * FROM ensemble_positions ORDER BY status = 'open' DESC, entry_session DESC, arm").map((r) => position(r, legs, marks, spots))
      : [];
    const counts = session === null ? new Map() : refusalCounts(db, session);
    const today =
      session !== null && hasTable(db, "ensemble_entries")
        ? rows(db, "SELECT * FROM ensemble_entries WHERE entry_session = ? ORDER BY arm, symbol", session).map((r) => entryRow(r, counts))
        : [];
    const iteration = hasTable(db, "ensemble_loop_iterations")
      ? db.prepare<[], Record<string, unknown>>("SELECT ran_at, phase, status FROM ensemble_loop_iterations ORDER BY ran_at DESC LIMIT 1").get()
      : undefined;
    const ranAt = iteration === undefined ? null : num(iteration["ran_at"]);
    return {
      session,
      dbPresent: true,
      arms: armsOf(db),
      positions,
      today,
      measurementBreaks: hasTable(db, "measurement_breaks")
        ? rows(db, "SELECT break_date, key, note FROM measurement_breaks ORDER BY break_date DESC").map((r) => ({
            date: str(r["break_date"]) ?? "",
            key: str(r["key"]) ?? "",
            note: str(r["note"]),
          }))
        : [],
      lastIteration:
        iteration === undefined || ranAt === null
          ? null
          : {
              ranAt,
              phase: str(iteration["phase"]) ?? "",
              status: str(iteration["status"]) ?? "",
              ageSeconds: Math.max(0, Date.now() / 1000 - ranAt),
            },
    };
  });
}

/** The session the decision journal's default read names: the loop's last run. */
export function resolveEnsembleSession(config: ConsoleConfig): string | null {
  return withReadOnlyDb<string | null>(dbPath(config), null, sessionOf);
}

// ------------------------------------------------------------------------------- history

/** How a structure ended: held to the posted close (`settled`) or closed by the arm's own exit. */
function exitKind(reason: string | null, status: string): EnsembleHistoryRow["exitKind"] {
  if (status !== "closed") return null;
  return reason === "settlement" ? "settled" : "closed";
}

/**
 * A closed structure in the trade table standard. Every figure is the module's own column: entry is
 * the cash at the mid (credit +, debit -), exit what closing or settling brought, so entry + exit =
 * gross; net is `net_pnl` (gross - fees - settlement - slippage). The three costs are three columns
 * in the ledger already, so nothing is split here. A missing cost reads null, which renders `n/r`.
 */
export function ensembleTradeCash(r: Record<string, unknown>, legs: EnsembleLeg[]): EnsembleHistoryRow {
  const entryCash = num(r["entry_value"]);
  return {
    positionId: str(r["position_id"]) ?? "",
    arm: str(r["arm"]) ?? "",
    symbol: str(r["symbol"]) ?? "",
    expiration: str(r["expiration"]) ?? "",
    entrySession: str(r["entry_session"]) ?? "",
    exitSession: str(r["exit_session"]),
    exitReason: str(r["exit_reason"]),
    quantity: num(r["quantity"]) ?? 1,
    // The one per-share figure: the structure's net price at the mid.
    price: num(r["entry_mid"]),
    entryCash: entryCash === null ? null : round2(entryCash),
    exitCash: num(r["exit_value"]),
    exitKind: exitKind(str(r["exit_reason"]), str(r["status"]) ?? ""),
    grossPnl: num(r["gross_pnl"]),
    fees: num(r["fees"]),
    settlementFees: num(r["settlement"]),
    slippage: num(r["slippage"]),
    netPnl: num(r["net_pnl"]),
    netStress: num(r["net_stress"]),
    netMid: num(r["net_mid"]),
    legs,
  };
}

export interface EnsembleHistoryFilter {
  arm: string | null;
  range?: DateRange;
}

/** Closed structures, newest exit first. A trade belongs to the session it ended, so the range
 *  bounds that, and the totals are over every structure the filter matches. */
export function readEnsembleHistory(config: ConsoleConfig, filter: EnsembleHistoryFilter): EnsembleHistory {
  const clauses = ["status = 'closed'"];
  const params: string[] = [];
  if (filter.arm !== null) {
    clauses.push("arm = ?");
    params.push(filter.arm);
  }
  const range = rangeClauses("COALESCE(exit_session, expiration)", filter.range);
  clauses.push(...range.clauses);
  params.push(...range.params);
  return withReadOnlyDb<EnsembleHistory>(dbPath(config), { rows: [], totals: EMPTY_TRADE_TOTALS }, (db) => {
    if (!hasTable(db, "ensemble_positions")) return { rows: [], totals: EMPTY_TRADE_TOTALS };
    const legs = legsByPosition(db);
    const out = rows(
      db,
      `SELECT * FROM ensemble_positions WHERE ${clauses.join(" AND ")} ORDER BY COALESCE(exit_session, expiration) DESC, entry_session DESC, arm`,
      ...params,
    ).map((r) => ensembleTradeCash(r, legs.get(str(r["position_id"]) ?? "") ?? entryLegs(str(r["entry_json"]))));
    return { rows: out, totals: tradeTotals(out) };
  });
}

// ---------------------------------------------------------------------------- performance

/** The largest peak-to-trough fall of the running sum, positive (0 when it never falls): `analytics.drawdown`. */
export function drawdown(nets: readonly number[]): number {
  let peak = 0;
  let run = 0;
  let worst = 0;
  for (const n of nets) {
    run += n;
    peak = Math.max(peak, run);
    worst = Math.max(worst, peak - run);
  }
  return round2(worst);
}

interface ClosedRow {
  arm: string;
  entrySession: string;
  exitSession: string;
  net: number;
  gross: number;
  fees: number;
  settlement: number;
  slippage: number;
  netStress: number;
  netMid: number;
}

const byExit = (a: ClosedRow, b: ClosedRow): number =>
  a.exitSession.localeCompare(b.exitSession) || a.entrySession.localeCompare(b.entrySession) || a.arm.localeCompare(b.arm);

/** `analytics._line`: one line over closed trades, in the order they ended. */
export function lineOf(closed: readonly ClosedRow[]): EnsembleLine {
  const ordered = [...closed].sort(byExit);
  const nets = ordered.map((r) => r.net);
  const sum = (pick: (r: ClosedRow) => number): number => round2(ordered.reduce((t, r) => t + pick(r), 0));
  let run = 0;
  return {
    trades: ordered.length,
    net: round2(nets.reduce((t, n) => t + n, 0)),
    gross: sum((r) => r.gross),
    fees: sum((r) => r.fees),
    settlement: sum((r) => r.settlement),
    slippage: sum((r) => r.slippage),
    netStress: sum((r) => r.netStress),
    netMid: sum((r) => r.netMid),
    wins: nets.filter((n) => n > 0).length,
    mean: ordered.length === 0 ? null : round2(nets.reduce((t, n) => t + n, 0) / ordered.length),
    worst: nets.length === 0 ? null : Math.min(...nets),
    drawdown: drawdown(nets),
    curve: ordered.map((r) => {
      run += r.net;
      return { x: r.exitSession, y: round2(run) };
    }),
  };
}

export function readEnsemblePerformance(config: ConsoleConfig): EnsemblePerformance {
  const empty: EnsemblePerformance = { dbPresent: false, arms: {}, combined: lineOf([]) };
  return withReadOnlyDb<EnsemblePerformance>(dbPath(config), empty, (db) => {
    if (!hasTable(db, "ensemble_positions")) return { ...empty, dbPresent: true };
    const closed: ClosedRow[] = rows(db, "SELECT * FROM ensemble_positions WHERE status = 'closed' AND net_pnl IS NOT NULL").map((r) => ({
      arm: str(r["arm"]) ?? "",
      entrySession: str(r["entry_session"]) ?? "",
      exitSession: str(r["exit_session"]) ?? "",
      net: num(r["net_pnl"]) ?? 0,
      gross: num(r["gross_pnl"]) ?? 0,
      fees: num(r["fees"]) ?? 0,
      settlement: num(r["settlement"]) ?? 0,
      slippage: num(r["slippage"]) ?? 0,
      netStress: num(r["net_stress"]) ?? 0,
      netMid: num(r["net_mid"]) ?? 0,
    }));
    const byArm = new Map<string, ClosedRow[]>();
    for (const r of closed) byArm.set(r.arm, [...(byArm.get(r.arm) ?? []), r]);
    // An arm with entries but no closed trade yet is still a line, of nothing.
    for (const a of armsOf(db)) if (!byArm.has(a)) byArm.set(a, []);
    return {
      dbPresent: true,
      arms: Object.fromEntries([...byArm.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([a, rs]) => [a, lineOf(rs)])),
      combined: lineOf(closed),
    };
  });
}

