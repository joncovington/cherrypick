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
  MorningVolRegime,
  MorningVolCurvePoint,
  MorningVolPercentile,
  MorningVolSeasonality,
  MorningRiskReversal,
  MorningRelease,
  MorningEarningsRow,
  MorningEarningsWeek,
  MorningFuture,
  MorningPremarket,
  MorningMoves,
  MorningYields,
  TechnicalsReport,
  TechnicalsBreadthDay,
  TechnicalsSectorStages,
  TechnicalsStageMember,
  TechnicalsLeader,
  TechnicalsMover,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str } from "./db.js";

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

function shapeVolPoint(raw: unknown): MorningVolCurvePoint {
  const c = rec(raw);
  return {
    point: str(c["point"]) ?? "unknown",
    symbol: str(c["symbol"]) ?? "?",
    dte: num(c["dte"]),
    value: num(c["value"]),
    basis: str(c["basis"]),
  };
}

function shapeVolPercentile(raw: unknown): MorningVolPercentile {
  const p = rec(raw);
  return {
    value: num(p["value"]),
    samples: num(p["samples"]),
    // Passed through, never recomputed here: the pack refuses a percentile under its own sample
    // floor, and a console that filled one in would be quietly overriding that refusal.
    percentile: num(p["percentile"]),
    reason: str(p["reason"]),
    source: str(p["source"]),
  };
}

function shapeVolSeasonality(raw: unknown): MorningVolSeasonality {
  const s = rec(raw);
  return {
    month: num(s["month"]),
    norm: num(s["norm"]),
    years: num(s["years"]),
    reason: str(s["reason"]),
    vixVsNormPct: num(s["vix_vs_norm_pct"]),
  };
}

function shapeRiskReversal(raw: unknown): MorningRiskReversal | null {
  // The pack writes null when no chain was on file.
  if (!raw || typeof raw !== "object") return null;
  const r = rec(raw);
  return {
    session: str(r["session"]),
    spot: num(r["spot"]),
    targetDte: num(r["target_dte"]),
    call25dIvPct: num(r["call_25d_iv_pct"]),
    put25dIvPct: num(r["put_25d_iv_pct"]),
    rrVolPts: num(r["rr_vol_pts"]),
    expirations: Array.isArray(r["expirations"])
      ? (r["expirations"] as unknown[]).flatMap((e) => {
          const x = rec(e);
          const expiration = str(x["expiration"]);
          return expiration === null ? [] : [{ expiration, dte: num(x["dte"]) }];
        })
      : [],
    source: str(r["source"]),
  };
}

function shapeVolRegime(raw: unknown): MorningVolRegime {
  const v = rec(raw);
  const slope = rec(v["slope"]);
  const shape = str(v["shape"]);
  const percentiles: Record<string, MorningVolPercentile> = {};
  for (const [key, entry] of Object.entries(rec(v["percentiles"]))) {
    percentiles[key] = shapeVolPercentile(entry);
  }
  return {
    curve: Array.isArray(v["curve"]) ? (v["curve"] as unknown[]).map(shapeVolPoint) : [],
    slope: {
      front9d30dPct: num(slope["front_9d_30d_pct"]),
      mid30d3mPct: num(slope["mid_30d_3m_pct"]),
      back9d1yPct: num(slope["back_9d_1y_pct"]),
    },
    vixVix3mRatio: num(v["vix_vix3m_ratio"]),
    // Only the two states the pack declares. Anything else is no shape at all rather than a guess —
    // the same discipline the deployment zone uses one function above.
    shape: shape === "contango" || shape === "backwardation" ? shape : null,
    shapeReason: str(v["shape_reason"]),
    percentiles,
    seasonality: v["seasonality"] !== undefined ? shapeVolSeasonality(v["seasonality"]) : null,
    riskReversal: shapeRiskReversal(v["risk_reversal_25d_30d"]),
    measuredPoints: num(v["measured_points"]),
    totalPoints: num(v["total_points"]),
    recordOnly: bool(v["record_only"]),
  };
}

function shapeRelease(raw: unknown): MorningRelease | null {
  const r = rec(raw);
  const name = str(r["name"]);
  const date = str(r["date"]);
  if (name === null || date === null) return null;
  return { name, date, timeEt: str(r["time_et"]), source: str(r["source"]), today: bool(r["today"]) };
}

function shapeEarningsRow(raw: unknown): MorningEarningsRow | null {
  const r = rec(raw);
  const symbol = str(r["symbol"]);
  const date = str(r["date"]);
  if (symbol === null || date === null) return null;
  return {
    symbol,
    date,
    when: str(r["when"]),
    expiration: str(r["expiration"]),
    spot: num(r["spot"]),
    strike: num(r["strike"]),
    straddle: num(r["straddle"]),
    expectedMove: num(r["expected_move"]),
    expectedMovePct: num(r["expected_move_pct"]),
    reason: str(r["reason"]),
  };
}

function shapeEarningsWeek(raw: unknown): MorningEarningsWeek {
  const e = rec(raw);
  return {
    rows: Array.isArray(e["rows"])
      ? (e["rows"] as unknown[]).map(shapeEarningsRow).filter((r): r is MorningEarningsRow => r !== null)
      : [],
    asOf: str(e["as_of"]),
    reason: str(e["reason"]),
  };
}

function shapeCalendar(raw: unknown): MorningCalendar {
  const c = rec(raw);
  return {
    // Fact version 4. Absent on older packs: null, so the page omits the table rather than
    // saying "none scheduled", which would be a claim the pack never made.
    releases: Array.isArray(c["releases"])
      ? (c["releases"] as unknown[]).map(shapeRelease).filter((r): r is MorningRelease => r !== null)
      : null,
    earnings: c["earnings"] !== undefined && c["earnings"] !== null ? shapeEarningsWeek(c["earnings"]) : null,
    isFomcDay: bool(c["is_fomc_day"]),
    nextFomc: str(c["next_fomc"]),
    fomcYearKnown: bool(c["fomc_year_known"]),
    isTripleWitching: bool(c["is_triple_witching"]),
    isQuarterlyExpiry: bool(c["is_quarterly_expiry"]),
    nextTradingDay: str(c["next_trading_day"]),
  };
}

function shapeFuture(raw: unknown): MorningFuture {
  const f = rec(raw);
  return {
    ...shapeReading(raw),
    product: str(f["product"]),
    priorSettle: num(f["prior_settle"]),
    priorSettleSession: str(f["prior_settle_session"]),
    changeVsPriorClosePct: num(f["change_vs_prior_close_pct"]),
    changeReason: str(f["change_reason"]),
  };
}

function shapePremarket(raw: unknown): MorningPremarket {
  const p = rec(raw);
  const futures: Record<string, MorningFuture> = {};
  for (const [key, entry] of Object.entries(rec(p["futures"]))) futures[key] = shapeFuture(entry);
  const indexes: Record<string, MorningReading> = {};
  for (const [key, entry] of Object.entries(rec(p["indexes"]))) indexes[key] = shapeReading(entry);
  return {
    futures,
    indexes,
    measuredFutures: num(p["measured_futures"]),
    recordOnly: bool(p["record_only"]),
  };
}

function shapeMoves(raw: unknown): MorningMoves {
  const m = rec(raw);
  const w = rec(m["weekly_expected_move"]);
  const rv = rec(m["realized_vol"]);
  return {
    weeklyExpectedMove: {
      pct: num(w["pct"]),
      points: num(w["points"]),
      vix: num(w["vix"]),
      basis: str(w["basis"]),
      reason: str(w["reason"]),
    },
    realizedVol: {
      pct: num(rv["pct"]),
      sessions: num(rv["sessions"]),
      through: str(rv["through"]),
      reason: str(rv["reason"]),
    },
    vixMinusRealized: num(m["vix_minus_realized"]),
  };
}

function numMap(raw: unknown): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  for (const [key, v] of Object.entries(rec(raw))) out[key] = num(v);
  return out;
}

function shapeYields(raw: unknown): MorningYields {
  const y = rec(raw);
  return {
    session: str(y["session"]),
    source: str(y["source"]),
    yields: numMap(y["yields"]),
    changeBp: numMap(y["change_bp"]),
    spread2s10sBp: num(y["spread_2s10s_bp"]),
    spread3m10yBp: num(y["spread_3m10y_bp"]),
    reason: str(y["reason"]),
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
    // Absent before fact version 3; null so the page omits the panel rather than drawing an
    // empty curve, which would read as a measured flat one.
    volRegime: facts["vol_regime"] !== undefined ? shapeVolRegime(facts["vol_regime"]) : null,
    calendar: facts["calendar"] !== undefined ? shapeCalendar(facts["calendar"]) : null,
    // Fact version 4. Absent on older packs; null so the page omits the card.
    premarket: facts["premarket"] !== undefined ? shapePremarket(facts["premarket"]) : null,
    moves: facts["moves"] !== undefined ? shapeMoves(facts["moves"]) : null,
    yields: facts["yields"] !== undefined ? shapeYields(facts["yields"]) : null,
  };
}

// --------------------------------------------------------------------------- technicals report
// `packages/technicals`' report artifact, passed through the same way: stages, breadth, rotation,
// signals and leaders are that package's answers, and nothing here re-derives or re-sorts them.

function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

function strListMap(raw: unknown): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [key, v] of Object.entries(rec(raw))) out[key] = strList(v);
  return out;
}

function members(v: unknown): TechnicalsStageMember[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((raw) => {
    const m = rec(raw);
    const symbol = str(m["symbol"]);
    return symbol === null ? [] : [{ symbol, stage: str(m["stage"]) }];
  });
}

function shapeBreadthDay(raw: unknown): TechnicalsBreadthDay[] {
  const b = rec(raw);
  const session = str(b["session"]);
  if (session === null) return [];
  return [
    {
      session,
      leaders: num(b["leaders"]),
      laggards: num(b["laggards"]),
      net: num(b["net"]),
      bullishShare: num(b["bullish_share"]),
    },
  ];
}

function shapeSectorStages(raw: unknown): TechnicalsSectorStages[] {
  const s = rec(raw);
  const sector = str(s["sector"]);
  if (sector === null) return [];
  return [{ sector, net: num(s["net"]), leaders: members(s["leaders"]), laggards: members(s["laggards"]) }];
}

function shapeLeader(raw: unknown): TechnicalsLeader[] {
  const l = rec(raw);
  const symbol = str(l["symbol"]);
  if (symbol === null) return [];
  return [
    {
      symbol,
      sector: str(l["sector"]),
      rank: num(l["rank"]),
      return6mPct: num(l["return_6m_pct"]),
      trendShort: str(l["trend_short"]),
      trendLong: str(l["trend_long"]),
      stage: str(l["stage"]),
    },
  ];
}

function shapeMover(raw: unknown): TechnicalsMover[] {
  const m = rec(raw);
  const symbol = str(m["symbol"]);
  if (symbol === null) return [];
  return [
    {
      symbol,
      sector: str(m["sector"]),
      changePct: num(m["change_pct"]),
      close: num(m["close"]),
      volumeRatio: num(m["volume_ratio"]),
      stage: str(m["stage"]),
    },
  ];
}

function list(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function shapeTechnicals(doc: Record<string, unknown>): TechnicalsReport | null {
  const session = str(doc["session"]);
  if (doc["ok"] !== true || session === null) return null;
  const rules: Record<string, string> = {};
  for (const [key, v] of Object.entries(rec(doc["rules"]))) if (typeof v === "string") rules[key] = v;
  return {
    session,
    reportVersion: num(doc["report_version"]),
    generatedAt: str(doc["generated_at"]),
    universe: num(doc["universe"]),
    rules,
    breadth: list(doc["breadth"]).flatMap(shapeBreadthDay),
    stages: list(doc["stages"]).flatMap(shapeSectorStages),
    rotation: strListMap(doc["rotation"]),
    signals: strListMap(doc["signals"]),
    leaders: list(doc["leaders"]).flatMap(shapeLeader),
    movers: {
      gainers: list(rec(doc["movers"])["gainers"]).flatMap(shapeMover),
      losers: list(rec(doc["movers"])["losers"]).flatMap(shapeMover),
    },
  };
}

/**
 * The newest technicals report dated strictly before `session`. A morning pack is written before
 * the open from the prior close; a report dated the pack's own day was written after that day's
 * close, and pairing the two would show the morning something it could not have known.
 */
export function readTechnicals(config: ConsoleConfig, session: string): TechnicalsReport | null {
  let days: string[];
  try {
    days = fs
      .readdirSync(config.paths.technicalsDir)
      .filter((f) => /^report-\d{4}-\d{2}-\d{2}\.json$/.test(f))
      .map((f) => f.slice(7, -5))
      .filter((d) => d < session)
      .sort();
  } catch {
    return null;
  }
  const day = days[days.length - 1];
  if (day === undefined) return null;
  try {
    const doc = JSON.parse(fs.readFileSync(path.join(config.paths.technicalsDir, `report-${day}.json`), "utf-8"));
    return shapeTechnicals(rec(doc));
  } catch {
    return null;
  }
}

export function readMorning(config: ConsoleConfig, session?: string): MorningPayload {
  const dir = config.paths.overviewDir;
  const sessions = listMorningSessions(config);
  const chosen = session && sessions.includes(session) ? session : sessions[sessions.length - 1];

  let current: MorningPack | null = null;
  let note: string | null = null;
  let technicals: TechnicalsReport | null = null;
  if (chosen) {
    const facts = readPack(dir, chosen);
    if (facts) current = shapePack(chosen, facts);
    note = readNote(dir, chosen);
    technicals = readTechnicals(config, chosen);
  }

  return { sessions, current, note, technicals };
}
