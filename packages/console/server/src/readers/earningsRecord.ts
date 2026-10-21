/**
 * The earnings calendar's forward record (2026-10-17; docs/earnings-screener-plan.md, step 5).
 *
 * `python -m cherrypick.technicals earnings-record` rebuilds `technicals/earnings-calendar/record.json`
 * each morning: every past announcement, what the calendar showed the session before, the move it
 * made against the expected move (0.85 x the straddle), whether a strong read called it, and the suggested spread's money on
 * its marks. This renames the fields and decides nothing.
 */

import fs from "node:fs";
import path from "node:path";
import type {
  EarningsEdge,
  EarningsRecordPayload,
  EarningsRecordRow,
  EarningsRecordSpread,
  EarningsRecordTotals,
  EarningsRecordTrade,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str } from "./db.js";
import { earningsCalendarDir } from "./earningsCalendar.js";

const EDGES = new Set<string>(["sb", "bn", "bp", "bi", "mx", "br", "bd", "brn", "sbe"]);

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

function trade(v: unknown): EarningsRecordTrade | null {
  const t = rec(v);
  const net = num(t["net"]);
  if (net === null) return null;
  return {
    entry: num(t["entry"]) ?? 0,
    exit: num(t["exit"]) ?? 0,
    gross: num(t["gross"]) ?? 0,
    fees: num(t["fees"]) ?? 0,
    slippage: num(t["slippage"]) ?? 0,
    net,
    risk: num(t["risk"]) ?? 0,
  };
}

function spread(v: unknown): EarningsRecordSpread | null {
  const s = rec(v);
  const state = str(s["state"]);
  if (state === null) return null;
  return {
    state,
    kind: s["kind"] === "debit" || s["kind"] === "credit" ? s["kind"] : null,
    right: s["right"] === "C" || s["right"] === "P" ? s["right"] : null,
    expiry: str(s["expiry"]),
    strikes: Array.isArray(s["strikes"]) ? s["strikes"].map((k) => num(k)).filter((k): k is number => k !== null) : null,
    entryMid: num(s["entry_mid"]),
    liquidAtEntry: bool(s["liquid_at_entry"]),
    react: trade(s["react"]),
    day2: trade(s["day2"]),
  };
}

function row(v: unknown): EarningsRecordRow | null {
  const r = rec(v);
  const symbol = str(r["symbol"]);
  const date = str(r["date"]);
  if (symbol === null || date === null) return null;
  const edge = str(r["edge"]);
  const region = r["region"];
  return {
    symbol,
    company: str(r["company"]),
    date,
    calendarDay: str(r["calendar_day"]) ?? "",
    timing: r["timing"] === "bmo" || r["timing"] === "amc" ? r["timing"] : null,
    reactionDay: str(r["reaction_day"]) ?? "",
    edge: edge !== null && EDGES.has(edge) ? (edge as EarningsEdge) : null,
    edgeLabel: str(r["edge_label"]),
    liquidityRank: num(r["liquidity_rank"]),
    region: region === "US" || region === "intl" ? region : null,
    movePct: num(r["move_pct"]),
    emMovePct: num(r["em_move_pct"]),
    eventExpiry: str(r["event_expiry"]),
    eventExpiryDays: num(r["event_expiry_days"]),
    moveOverEm: num(r["move_over_em"]),
    insideEm: bool(r["inside_em"]),
    directionRight: bool(r["direction_right"]),
    spread: spread(r["spread"]),
  };
}

function totals(v: unknown): EarningsRecordTotals {
  const t = rec(v);
  return { n: num(t["n"]) ?? 0, net: num(t["net"]) ?? 0, won: num(t["won"]), returnOnRisk: num(t["return_on_risk"]) };
}

export function readEarningsRecord(config: ConsoleConfig): EarningsRecordPayload {
  let doc: Record<string, unknown>;
  try {
    doc = rec(JSON.parse(fs.readFileSync(path.join(earningsCalendarDir(config), "record.json"), "utf-8")));
  } catch {
    return { asOf: null, summary: null, rows: [] };
  }
  const s = rec(doc["summary"]);
  const wk = rec(s["earnings_week"]);
  const wkDays = num(wk["max_days"]);
  return {
    asOf: str(doc["as_of"]),
    summary: {
      announcements: num(s["announcements"]) ?? 0,
      measuredMoves: num(s["measured_moves"]),
      // An older record called the compared count `measured`.
      compared: num(s["compared"]) ?? num(s["measured"]) ?? 0,
      insideEm: num(s["inside_em"]),
      medianMoveOverEm: num(s["median_move_over_em"]),
      earningsWeek:
        wkDays === null
          ? null
          : { maxDays: wkDays, n: num(wk["n"]) ?? 0, insideEm: num(wk["inside_em"]), medianMoveOverEm: num(wk["median_move_over_em"]) },
      strongReads: num(s["strong_reads"]) ?? 0,
      directionRight: num(s["direction_right"]),
      react: totals(s["spreads_react"]),
      day2: totals(s["spreads_day2"]),
    },
    rows: (Array.isArray(doc["rows"]) ? doc["rows"].map(row).filter((r): r is EarningsRecordRow => r !== null) : []).reverse(),
  };
}
