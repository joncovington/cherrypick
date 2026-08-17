/**
 * The morning report: read `packages/overview`'s fact pack, render nothing new.
 *
 * Same posture as `review.ts`, for the same reason: the pack precomputes everything a reader could
 * be tempted to re-derive — the phase verdict, each gate's met/not_met/unknown, the strongest and
 * weakest sectors — so this reader parses, shapes for the page, and passes through. Recomputing any
 * of it here would be a second opinion waiting to drift from the artifact the markdown render and
 * the narrative were written against.
 *
 * Fields are read defensively: the artifact is versioned and will gain fields, and a console that
 * throws on an unfamiliar shape would take the page down for an additive change. And null is never
 * coerced to 0 anywhere — an unmeasured reading stays null all the way to the em dash.
 */

import fs from "node:fs";
import path from "node:path";
import type {
  MorningPayload,
  MorningPack,
  MorningReading,
  MorningLevels,
  MorningSectors,
  MorningSectorRow,
  MorningGate,
  MorningPhase,
  MorningDeployment,
  MorningSignal,
  MorningCalendar,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function readPack(dir: string, session: string): Record<string, unknown> | null {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, `morning-${session}.json`), "utf-8"));
  } catch {
    return null;
  }
}

function readNote(dir: string, session: string): string | null {
  try {
    return fs.readFileSync(path.join(dir, `morning-${session}.note.md`), "utf-8");
  } catch {
    return null;
  }
}

export function listMorningSessions(config: ConsoleConfig): string[] {
  try {
    return fs
      .readdirSync(config.paths.overviewDir)
      .filter((f) => f.startsWith("morning-") && f.endsWith(".json"))
      .map((f) => f.slice(8, -5))
      .sort();
  } catch {
    return [];
  }
}

function shapeReading(raw: unknown): MorningReading {
  const r = rec(raw);
  return {
    value: num(r["value"]),
    basis: str(r["basis"]),
    session: str(r["session"]),
    asOf: str(r["as_of"]),
    source: str(r["source"]),
    label: str(r["label"]),
    priorClose: num(r["prior_close"]),
    priorChangePct: num(r["prior_change_pct"]),
  };
}

function shapeLevels(raw: unknown): MorningLevels {
  const l = rec(raw);
  return {
    symbol: str(l["symbol"]),
    referencePrice: num(l["reference_price"]),
    referenceBasis: str(l["reference_basis"]),
    zeroGamma: num(l["zero_gamma"]),
    callWall: num(l["call_wall"]),
    putWall: num(l["put_wall"]),
    netGex: num(l["net_gex"]),
    session: str(l["session"]),
    asOf: str(l["as_of"]),
    source: str(l["source"]),
  };
}

function shapeSectorRow(raw: unknown): MorningSectorRow | null {
  const r = rec(raw);
  const symbol = str(r["symbol"]);
  if (symbol === null) return null;
  return {
    symbol,
    sector: str(r["sector"]),
    changePct: num(r["change_pct"]),
    close: num(r["close"]),
    session: str(r["session"]),
  };
}

function shapeSectors(raw: unknown): MorningSectors {
  const s = rec(raw);
  const board = Array.isArray(s["board"])
    ? (s["board"] as unknown[]).map(shapeSectorRow).filter((r): r is MorningSectorRow => r !== null)
    : [];
  return {
    board,
    // Strongest/weakest come from the pack, never re-derived from the board here.
    strongest: shapeSectorRow(s["strongest"]),
    weakest: shapeSectorRow(s["weakest"]),
    measured: num(s["measured"]),
  };
}

function shapeGate(raw: unknown): MorningGate {
  const g = rec(raw);
  const status = str(g["status"]);
  return {
    id: str(g["id"]) ?? "unknown",
    label: str(g["label"]) ?? str(g["id"]) ?? "unknown gate",
    // Anything unfamiliar reads as unknown — an unrecognised verdict must never render as met.
    status: status === "met" || status === "not_met" ? status : "unknown",
    value: num(g["value"]),
    threshold: num(g["threshold"]),
    detail: str(g["detail"]),
  };
}

function shapePhase(raw: unknown): MorningPhase | null {
  const p = rec(raw);
  const phase = str(p["phase"]);
  if (phase !== "green" && phase !== "yellow" && phase !== "red") return null;
  return {
    phase,
    reason: str(p["reason"]),
    gatesTotal: num(p["gates_total"]),
    gatesMeasured: num(p["gates_measured"]),
    gatesMet: num(p["gates_met"]),
  };
}

function shapeSignal(raw: unknown): MorningSignal {
  const s = rec(raw);
  const status = str(s["status"]);
  return {
    id: str(s["id"]) ?? "unknown",
    label: str(s["label"]) ?? str(s["id"]) ?? "unknown signal",
    // Anything unfamiliar reads as unknown — an unrecognised status must never render as measured.
    status: status === "measured" ? status : "unknown",
    score: num(s["score"]),
    value: num(s["value"]),
    weight: num(s["weight"]),
    detail: str(s["detail"]),
  };
}

function shapeDeployment(raw: unknown): MorningDeployment {
  const d = rec(raw);
  const zone = str(d["zone"]);
  return {
    score: num(d["score"]),
    // Only the three zones the pack declares; anything else is no zone at all, never a guess.
    zone: zone === "full" || zone === "reduced" || zone === "defensive" ? zone : null,
    signals: Array.isArray(d["signals"]) ? (d["signals"] as unknown[]).map(shapeSignal) : [],
    signalsMeasured: num(d["signals_measured"]),
    signalsTotal: num(d["signals_total"]),
    weightsRenormalized: bool(d["weights_renormalized"]),
    deferred: Array.isArray(d["deferred"])
      ? (d["deferred"] as unknown[]).filter((v): v is string => typeof v === "string")
      : [],
    reason: str(d["reason"]),
    note: str(d["note"]),
  };
}

function shapeCalendar(raw: unknown): MorningCalendar {
  const c = rec(raw);
  return {
    isFomcDay: bool(c["is_fomc_day"]),
    nextFomc: str(c["next_fomc"]),
    fomcYearKnown: bool(c["fomc_year_known"]),
    isTripleWitching: bool(c["is_triple_witching"]),
    isQuarterlyExpiry: bool(c["is_quarterly_expiry"]),
    nextTradingDay: str(c["next_trading_day"]),
  };
}

function shapePack(session: string, facts: Record<string, unknown>): MorningPack {
  const readings: Record<string, MorningReading> = {};
  for (const [key, raw] of Object.entries(rec(facts["readings"]))) {
    readings[key] = shapeReading(raw);
  }
  return {
    session,
    factVersion: num(facts["fact_version"]),
    generatedAt: str(facts["generated_at"]),
    readings,
    levels: facts["levels"] !== undefined ? shapeLevels(facts["levels"]) : null,
    sectors: facts["sectors"] !== undefined ? shapeSectors(facts["sectors"]) : null,
    gates: Array.isArray(facts["gates"]) ? (facts["gates"] as unknown[]).map(shapeGate) : [],
    phase: shapePhase(facts["phase"]),
    // Absent on pre-v2 packs; null so the page omits the card rather than rendering an empty one.
    deployment: facts["deployment"] !== undefined ? shapeDeployment(facts["deployment"]) : null,
    calendar: facts["calendar"] !== undefined ? shapeCalendar(facts["calendar"]) : null,
  };
}

export function readMorning(config: ConsoleConfig, session?: string): MorningPayload {
  const dir = config.paths.overviewDir;
  const sessions = listMorningSessions(config);
  const chosen = session && sessions.includes(session) ? session : sessions[sessions.length - 1];

  let current: MorningPack | null = null;
  let note: string | null = null;
  if (chosen) {
    const facts = readPack(dir, chosen);
    if (facts) current = shapePack(chosen, facts);
    note = readNote(dir, chosen);
  }

  return { sessions, current, note };
}
