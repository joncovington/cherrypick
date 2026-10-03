/**
 * The Options flow page: QuikOptions' Hot Options Report as `scripts/fetch_quikoptions.py` saved it,
 * one JSON file a session under `data/quikoptions/hot-options/`.
 *
 * The posture is `overview.ts`'s: the capture already validated the page and derived everything a
 * reader could be tempted to compute (outright premium, a spread's direction, which trades print
 * together, the names across tables, premium by the site's side), so this reader parses, renames to
 * the console's spelling, and passes through. A `.rejected` capture is never shown: it failed the
 * page's own identities. Null is never coerced to 0, and an absent store says so in `degraded`
 * rather than looking like a day with no flow.
 */

import fs from "node:fs";
import path from "node:path";
import type {
  DerivedFlow,
  DerivedFlowChecks,
  DerivedFlowName,
  DerivedFlowRow,
  FlowBirdseyeRow,
  FlowName,
  FlowSide,
  FlowSpread,
  FlowTrade,
  FlowVolOi,
  MorningFlowMarks,
  OptionsFlowDay,
  OptionsFlowPayload,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str } from "./db.js";

const DAY_FILE = /^(\d{4}-\d{2}-\d{2})\.json$/;

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function list(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function numMap(v: unknown): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  for (const [k, x] of Object.entries(rec(v))) out[k] = num(x);
  return out;
}

function strMap(v: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, x] of Object.entries(rec(v))) if (typeof x === "string") out[k] = x;
  return out;
}

function cp(v: unknown): "call" | "put" | null {
  return v === "call" || v === "put" ? v : null;
}

export function flowDir(config: ConsoleConfig): string {
  return path.join(config.paths.quikoptionsDir ?? path.join(config.paths.cherrypick, "data", "quikoptions"), "hot-options");
}

/** Every session with a capture that passed, oldest first. A `.rejected` day is not one. */
export function listFlowSessions(config: ConsoleConfig): string[] {
  try {
    return fs
      .readdirSync(flowDir(config))
      .map((f) => DAY_FILE.exec(f)?.[1])
      .filter((d): d is string => d !== undefined)
      .sort();
  } catch {
    return [];
  }
}

function shapeSide(v: unknown): FlowSide | null {
  const s = rec(v);
  const sentiment = str(s["sentiment"]);
  const fill = str(s["fill"]);
  return sentiment !== null && fill !== null ? { sentiment, fill, edge: num(s["edge"]) } : null;
}

function shapeBirdseye(v: unknown): FlowBirdseyeRow {
  const r = rec(v);
  return {
    symbol: str(r["symbol"]) ?? "?",
    name: str(r["name"]),
    buckets: numMap(r["buckets"]),
    bands: numMap(r["bands"]),
    calls: num(r["calls"]),
    puts: num(r["puts"]),
    total: num(r["total"]),
    callShare: num(r["call_share"]),
    shown: strMap(r["shown"]),
  };
}

function shapeTrade(v: unknown): FlowTrade {
  const r = rec(v);
  return {
    symbol: str(r["symbol"]) ?? "?",
    name: str(r["name"]),
    timeEt: str(r["time_et"]),
    size: num(r["size"]),
    expires: str(r["expires"]),
    strike: num(r["strike"]),
    cp: cp(r["cp"]),
    price: num(r["price"]),
    premium: num(r["premium"]),
    premiumDerived: r["premium_derived"] === true,
    side: shapeSide(r["side"]),
  };
}

function shapeSpread(v: unknown): FlowSpread {
  const r = rec(v);
  const u = rec(r["underlying"]);
  const direction = r["direction"] === "bought" || r["direction"] === "sold" ? r["direction"] : null;
  return {
    symbol: str(r["symbol"]) ?? "?",
    name: str(r["name"]),
    timeEt: str(r["time_et"]),
    size: num(r["size"]),
    expires: str(r["expires"]),
    type: str(r["type"]),
    cp: cp(r["cp"]),
    spread: str(r["spread"]),
    price: num(r["price"]),
    delta: num(r["delta"]),
    premium: num(r["premium"]),
    direction,
    group: str(r["group"]),
    underlying: r["underlying"] === undefined ? null : { last: num(u["last"]), bid: num(u["bid"]), ask: num(u["ask"]) },
    exchange: str(r["exchange"]),
  };
}

function shapeVolOi(v: unknown): FlowVolOi {
  const r = rec(v);
  return {
    symbol: str(r["symbol"]) ?? "?",
    name: str(r["name"]),
    volume: num(r["volume"]),
    oi: num(r["oi"]),
    vOi: num(r["v_oi"]),
    expires: str(r["expires"]),
    strike: num(r["strike"]),
    cp: cp(r["cp"]),
  };
}

function shapeName(v: unknown): FlowName {
  const r = rec(v);
  return {
    symbol: str(r["symbol"]) ?? "?",
    name: str(r["name"]),
    tables: list(r["tables"]).filter((t): t is string => typeof t === "string"),
  };
}

function shapeLargest(v: unknown): OptionsFlowDay["largestTrade"] {
  const r = rec(v);
  const premium = num(r["premium"]);
  const table = str(r["table"]);
  return premium === null || table === null ? null : { table, symbol: str(r["symbol"]), premium };
}

function shapeDerivedRow(v: unknown): DerivedFlowRow {
  const r = rec(v);
  const f = rec(r["factors"]);
  const kind = r["kind"] === "sweep" || r["kind"] === "spread" ? r["kind"] : "outright";
  const view = r["view"] === "bullish" || r["view"] === "bearish" ? r["view"] : null;
  const direction = r["direction"] === "bought" || r["direction"] === "sold" ? r["direction"] : null;
  const c = r["confirmed"];
  return {
    kind,
    symbol: str(r["symbol"]) ?? "?",
    what: str(r["what"]),
    size: num(r["size"]),
    premium: num(r["premium"]),
    direction,
    view,
    delta: num(r["delta"]),
    deltaDollars: num(r["delta_dollars"]),
    days: num(r["days"]),
    flags: list(r["flags"]).filter((x): x is string => typeof x === "string"),
    factors:
      r["factors"] === undefined
        ? null
        : { size: num(f["size"]) ?? 0, conviction: num(f["conviction"]) ?? 0, purity: num(f["purity"]) ?? 0, opening: num(f["opening"]) ?? 0 },
    score: num(r["score"]),
    confirmed: c === "opened" || c === "closed" || c === "mixed" ? c : null,
    confirmedScore: num(r["confirmed_score"]),
  };
}

function shapeDerivedName(v: unknown): DerivedFlowName {
  const r = rec(v);
  return {
    symbol: str(r["symbol"]) ?? "?",
    flows: num(r["flows"]) ?? 0,
    bullish: num(r["bullish"]) ?? 0,
    bearish: num(r["bearish"]) ?? 0,
    net: num(r["net"]) ?? 0,
    unread: num(r["unread"]) ?? 0,
    top: num(r["top"]) ?? 0,
  };
}

function readJson(file: string): Record<string, unknown> | null {
  try {
    return rec(JSON.parse(fs.readFileSync(file, "utf-8")));
  } catch {
    return null;
  }
}

function strList(v: unknown): string[] {
  return list(v).filter((x): x is string => typeof x === "string");
}

/** The day's checks from its flow file, and the running audit and review from their summaries. */
function shapeChecks(config: ConsoleConfig, doc: Record<string, unknown>): DerivedFlowChecks {
  const c = rec(doc["checks"]);
  const vote = c["site_vote"] === undefined ? null : rec(c["site_vote"]);
  const delta = c["delta"] === undefined ? null : rec(c["delta"]);
  const close = c["close"] === undefined ? null : rec(c["close"]);
  const root = path.dirname(flowDir(config));
  const audit = readJson(path.join(root, "audit-summary.json"));
  const review = readJson(path.join(root, "review.json"));
  return {
    siteVote: vote && { agrees: num(vote["agrees"]) ?? 0, neutral: num(vote["neutral"]) ?? 0, opposite: num(vote["opposite"]) ?? 0 },
    delta: delta && { broker: num(delta["broker"]) ?? 0, singles: num(delta["singles"]) ?? 0, off: strList(delta["off"]) },
    close: close && { compared: num(close["compared"]) ?? 0, of: num(close["of"]) ?? 0, off: strList(close["off"]), note: str(close["note"]) },
    audit: audit && { checked: num(audit["checked"]) ?? 0, agree: num(audit["agree"]) ?? 0, rate: num(audit["rate"]) },
    review: review && {
      sessions: num(review["sessions"]) ?? 0,
      needed: num(review["needed"]) ?? 0,
      passed: typeof review["passed"] === "boolean" ? review["passed"] : null,
    },
  };
}

/** The session's scored derived flows (`<session>.flow.json`), or null when not scored yet. */
function readDerived(config: ConsoleConfig, session: string): DerivedFlow | null {
  let doc: Record<string, unknown>;
  try {
    doc = rec(JSON.parse(fs.readFileSync(path.join(flowDir(config), `${session}.flow.json`), "utf-8")));
  } catch {
    return null;
  }
  return {
    checks: shapeChecks(config, doc),
    scoredAt: str(doc["scored_at"]),
    confirmedAt: str(doc["confirmed_at"]),
    flows: list(doc["flows"]).map(shapeDerivedRow),
    unread: list(doc["unread"]).map(shapeDerivedRow),
    names: list(doc["names"]).map(shapeDerivedName),
  };
}

function readDoc(config: ConsoleConfig, session: string): Record<string, unknown> | null {
  try {
    return rec(JSON.parse(fs.readFileSync(path.join(flowDir(config), `${session}.json`), "utf-8")));
  } catch {
    return null;
  }
}

export function shapeFlowDay(session: string, doc: Record<string, unknown>): OptionsFlowDay {
  const t = rec(doc["tables"]);
  const d = rec(doc["derived"]);
  const premiumBySide: Record<string, number> = {};
  for (const [k, v] of Object.entries(numMap(d["premium_by_side"]))) if (v !== null) premiumBySide[k] = v;
  const tradesBySide: Record<string, number> = {};
  for (const [k, v] of Object.entries(numMap(d["trades_by_side"]))) if (v !== null) tradesBySide[k] = v;
  return {
    session,
    savedAt: str(doc["saved_at"]),
    birdseye: list(t["birdseye"]).map(shapeBirdseye),
    outrights: list(t["outrights"]).map(shapeTrade),
    sweeps: list(t["sweeps"]).map(shapeTrade),
    spreads: list(t["spreads"]).map(shapeSpread),
    voloi: list(t["voloi"]).map(shapeVolOi),
    openings: list(t["openings"]).map(shapeVolOi),
    names: list(d["names"]).map(shapeName),
    premiumBySide,
    tradesBySide,
    largestTrade: shapeLargest(d["largest_trade"]),
    derived: null, // attached by readOptionsFlow, from the session's own .flow.json
  };
}

export function readOptionsFlow(config: ConsoleConfig, session?: string): OptionsFlowPayload {
  const sessions = listFlowSessions(config);
  if (sessions.length === 0) {
    return { sessions, current: null, degraded: { reason: "no capture yet (scripts/fetch_quikoptions.py hot-options)" } };
  }
  const chosen = session !== undefined && sessions.includes(session) ? session : sessions[sessions.length - 1]!;
  const doc = readDoc(config, chosen);
  if (doc === null) return { sessions, current: null, degraded: { reason: `the ${chosen} capture could not be read` } };
  return { sessions, current: { ...shapeFlowDay(chosen, doc), derived: readDerived(config, chosen) } };
}

/**
 * The flow tables each name of a session appears in, for the Morning report's OCC hot-options card
 * to mark. Null when that session has no capture: no marks, never "no flow".
 */
export function readFlowMarks(config: ConsoleConfig, session: string | null): MorningFlowMarks | null {
  if (session === null || !listFlowSessions(config).includes(session)) return null;
  const doc = readDoc(config, session);
  if (doc === null) return null;
  const tables: Record<string, string[]> = {};
  for (const [name, rows] of Object.entries(rec(doc["tables"]))) {
    for (const row of list(rows)) {
      const sym = str(rec(row)["symbol"]);
      if (sym === null) continue;
      const seen = (tables[sym] ??= []);
      if (!seen.includes(name)) seen.push(name);
    }
  }
  return { session, tables };
}
