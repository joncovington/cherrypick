/**
 * The earnings calendar for the Screeners page's earnings tab (2026-10-17;
 * docs/earnings-screener-plan.md).
 *
 * `python -m cherrypick.technicals earnings-calendar` writes one file a session to
 * `technicals/earnings-calendar/<day>.json`, already joined: dates and times, the security list's
 * direction, strength and IV rank, the one-SD expected move, and the navigator's suggested spread.
 * This reads that file and renames its fields; it decides nothing, so a broken join shows here as
 * the same rows the file holds.
 */

import fs from "node:fs";
import path from "node:path";
import type { EarningsCalendarPayload, EarningsCalendarRow, EarningsEdge, EarningsSuggestion } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str } from "./db.js";

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
    suggestion: suggestion(r["suggestion"]),
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
  const rows = Array.isArray(doc["rows"]) ? doc["rows"].map(row).filter((r): r is EarningsCalendarRow => r !== null) : [];
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
