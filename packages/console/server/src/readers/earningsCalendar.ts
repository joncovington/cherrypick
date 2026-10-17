/**
 * The earnings calendar for the Screeners page's earnings tab (2026-10-17;
 * docs/earnings-screener-plan.md).
 *
 * `python -m cherrypick.technicals earnings-calendar` writes one file a session to
 * `technicals/earnings-calendar/<day>.json`, already joined: dates and times, our direction and
 * strength, the broker's IV rank, the expected move, and the navigator's suggested spread. The
 * vendor's reads ride along in the file as `vendor_*` and are not read here.
 * This reads that file and renames its fields; it decides nothing, so a broken join shows here as
 * the same rows the file holds.
 *
 * Joined to it, as the income tabs join their leg readings: the suggested spread on listed strikes,
 * quoted in regular hours and judged against the leg bars by `fetch_screener_greeks.py measure`
 * (`market-report/screener-greeks/earnings-legs/<date>/<HHMM>.json`) -- the latest reading taken OF
 * this calendar (`calendar_day`), not merely the newest file.
 */

import fs from "node:fs";
import path from "node:path";
import type {
  EarningsCalendarPayload,
  EarningsCalendarRow,
  EarningsEdge,
  EarningsSuggestion,
  EarningsTrade,
  EarningsTradeLeg,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str } from "./db.js";
import { marketReportDir } from "./screeners.js";

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const EDGES = new Set<EarningsEdge>(["sb", "bn", "bp", "bi", "mx", "br", "bd", "brn", "sbe"]);

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

export function earningsCalendarDir(config: ConsoleConfig): string {
  return path.join(config.paths.technicalsDir, "earnings-calendar");
}

export function earningsCalendarDays(config: ConsoleConfig): string[] {
  try {
    return fs
      .readdirSync(earningsCalendarDir(config))
      .filter((n) => n.endsWith(".json") && DAY.test(n.slice(0, -5)))
      .map((n) => n.slice(0, -5))
      .sort()
      .reverse();
  } catch {
    return [];
  }
}

function suggestion(v: unknown): EarningsSuggestion | null {
  const s = rec(v);
  const long = num(s["long"]);
  const short = num(s["short"]);
  if ((s["kind"] !== "debit" && s["kind"] !== "credit") || long === null || short === null) return null;
  return { kind: s["kind"], right: s["right"] === "C" ? "C" : "P", long, short, expiry: str(s["expiry"]) };
}

function tradeLeg(v: unknown): EarningsTradeLeg | null {
  const l = rec(v);
  const strike = num(l["strike"]);
  if (strike === null) return null;
  const q = rec(l["quote"]);
  return {
    role: l["role"] === "short" ? "short" : "long",
    strike,
    bid: num(q["bid"]),
    ask: num(q["ask"]),
    openInterest: num(q["open_interest"]),
    volume: num(q["volume"]),
    passes: bool(l["passes"]),
  };
}

/** The latest earnings-legs reading taken of calendar `day`, by symbol. Readings sit under the date
 *  they were TAKEN (a session after the calendar), naming the calendar day they read. */
export function readTrades(config: ConsoleConfig, day: string): Map<string, EarningsTrade> {
  const dir = path.join(marketReportDir(config), "screener-greeks", "earnings-legs");
  const files: string[] = [];
  try {
    for (const d of fs.readdirSync(dir).filter((n) => DAY.test(n) && n >= day)) {
      for (const f of fs.readdirSync(path.join(dir, d)).filter((n) => n.endsWith(".json"))) files.push(path.join(dir, d, f));
    }
  } catch {
    return new Map();
  }
  for (const file of files.sort().reverse()) {
    let doc: Record<string, unknown>;
    try {
      doc = rec(JSON.parse(fs.readFileSync(file, "utf-8")));
    } catch {
      continue;
    }
    if (doc["calendar_day"] !== day) continue;
    const at = str(doc["measured_at"]);
    const out = new Map<string, EarningsTrade>();
    for (const raw of Array.isArray(doc["rows"]) ? doc["rows"] : []) {
      const r = rec(raw);
      const symbol = str(r["symbol"]);
      const expiry = str(r["expiry"]);
      const width = num(r["width"]);
      const legs = Array.isArray(r["legs"]) ? r["legs"].map(tradeLeg).filter((l): l is EarningsTradeLeg => l !== null) : [];
      if (symbol === null || expiry === null || width === null || legs.length !== 2) continue;
      out.set(symbol, {
        readingAt: at,
        kind: r["kind"] === "debit" ? "debit" : "credit",
        right: r["right"] === "C" ? "C" : "P",
        expiry,
        width,
        legs,
        mid: num(r["mid"]),
        natural: num(r["natural"]),
        shareOfWidth: num(r["share_of_width"]),
        maxLoss: num(r["max_loss"]),
        passes: bool(r["passes"]),
      });
    }
    return out;
  }
  return new Map();
}

/** The evening's listed strikes for calendar `day` (`fetch_screener_greeks.py strikes`, written to
 *  `earnings-strikes/<date>.json`): the newest file naming that calendar, by symbol, [long, short]. */
export function readListedStrikes(config: ConsoleConfig, day: string): Map<string, { expiry: string; strikes: number[] }> {
  const dir = path.join(marketReportDir(config), "screener-greeks", "earnings-strikes");
  let names: string[];
  try {
    names = fs.readdirSync(dir).filter((n) => n.endsWith(".json") && DAY.test(n.slice(0, -5)) && n.slice(0, -5) >= day);
  } catch {
    return new Map();
  }
  for (const name of names.sort().reverse()) {
    let doc: Record<string, unknown>;
    try {
      doc = rec(JSON.parse(fs.readFileSync(path.join(dir, name), "utf-8")));
    } catch {
      continue;
    }
    if (doc["calendar_day"] !== day) continue;
    const out = new Map<string, { expiry: string; strikes: number[] }>();
    for (const raw of Array.isArray(doc["rows"]) ? doc["rows"] : []) {
      const r = rec(raw);
      const symbol = str(r["symbol"]);
      const expiry = str(r["expiry"]);
      const strikes = Array.isArray(r["legs"]) ? r["legs"].map((l) => num(rec(l)["strike"])).filter((k): k is number => k !== null) : [];
      if (symbol !== null && expiry !== null && strikes.length === 2) out.set(symbol, { expiry, strikes });
    }
    return out;
  }
  return new Map();
}

function row(v: unknown): EarningsCalendarRow | null {
  const r = rec(v);
  const symbol = str(r["symbol"]);
  const date = str(r["date"]);
  const reactionDay = str(r["reaction_day"]);
  if (symbol === null || date === null || reactionDay === null) return null;
  const edge = str(r["edge"]);
  const region = r["region"];
  return {
    symbol,
    company: str(r["company"]),
    date,
    timing: r["timing"] === "bmo" || r["timing"] === "amc" ? r["timing"] : null,
    timingSource:
      r["timing_source"] === "broker" || r["timing_source"] === "nasdaq" || r["timing_source"] === "dolt"
        ? r["timing_source"]
        : null,
    usualTiming: r["usual_timing"] === "bmo" || r["usual_timing"] === "amc" ? r["usual_timing"] : null,
    dateSource: r["date_source"] === "dolt" ? "dolt" : "broker",
    dateConfirmed: bool(r["date_confirmed"]),
    dateGapDays: num(r["date_gap_days"]),
    entrySession: str(r["entry_session"]),
    reactionDay,
    type: str(r["type"]),
    region: region === "US" || region === "intl" ? region : null,
    edge: edge !== null && EDGES.has(edge as EarningsEdge) ? (edge as EarningsEdge) : null,
    edgeLabel: str(r["edge_label"]),
    strength: num(r["strength"]),
    readSession: str(r["read_session"]),
    ivRank: num(r["iv_rank"]),
    liquidityRank: num(r["liquidity_rank"]),
    optionable: bool(r["optionable"]),
    sector: str(r["sector"]),
    price: num(r["price"]),
    eventExpiry: str(r["event_expiry"]),
    eventIv: num(r["event_iv"]),
    sdMove: num(r["sd_move"]),
    sdMovePct: num(r["sd_move_pct"]),
    emMove: num(r["em_move"]),
    emMovePct: num(r["em_move_pct"]),
    suggestion: suggestion(r["suggestion"]),
    trade: null,
    listed: null,
  };
}

export function readEarningsCalendar(config: ConsoleConfig, wanted?: string | null): EarningsCalendarPayload {
  const days = earningsCalendarDays(config);
  const day = wanted && days.includes(wanted) ? wanted : (days[0] ?? null);
  const empty: EarningsCalendarPayload = {
    day,
    days,
    inputs: { securityList: null, expiryIv: null, brokerDates: null, nasdaqTimes: null, closeDate: null },
    ivRankCut: null,
    rows: [],
  };
  if (day === null) return empty;
  let doc: Record<string, unknown>;
  try {
    doc = rec(JSON.parse(fs.readFileSync(path.join(earningsCalendarDir(config), `${day}.json`), "utf-8")));
  } catch {
    return empty;
  }
  const inputs = rec(doc["inputs"]);
  const trades = readTrades(config, day);
  const listed = readListedStrikes(config, day);
  const rows = (Array.isArray(doc["rows"]) ? doc["rows"].map(row).filter((r): r is EarningsCalendarRow => r !== null) : []).map(
    (r) => (r.suggestion ? { ...r, trade: trades.get(r.symbol) ?? null, listed: listed.get(r.symbol) ?? null } : r),
  );
  return {
    day,
    days,
    inputs: {
      securityList: str(inputs["security_list"]),
      expiryIv: str(inputs["expiry_iv"]),
      brokerDates: str(inputs["broker_dates"]),
      nasdaqTimes: str(inputs["nasdaq_times"]),
      closeDate: str(inputs["close_date"]),
    },
    ivRankCut: num(rec(doc["settings"])["iv_rank_cut"]),
    rows,
  };
}
