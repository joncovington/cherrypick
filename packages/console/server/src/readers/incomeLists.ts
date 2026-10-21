/**
 * Our own short-put and covered-call lists: `packages/technicals` `income-lists` writes one file a
 * session (`technicals/income-lists/<session>.json`); this renames its rows and decides nothing. The
 * strike, delta, working price, return, liquidity verdict and earnings state are all the builder's.
 * A `*.trial.json` is never read.
 */
import fs from "node:fs";
import path from "node:path";
import type { IncomeEarnings, IncomeList, IncomeListPayload, IncomeListRow } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str } from "./db.js";

export const INCOME_LISTS: readonly IncomeList[] = ["short-puts", "covered-calls"];
const DAY = /^\d{4}-\d{2}-\d{2}$/;
const EARNINGS: readonly IncomeEarnings[] = ["in-trade", "clear", "unknown", "none"];

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function readJson(file: string): Record<string, unknown> | null {
  try {
    return rec(JSON.parse(fs.readFileSync(file, "utf-8")));
  } catch {
    return null;
  }
}

function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

function days(dir: string): string[] {
  try {
    return fs
      .readdirSync(dir)
      .filter((n) => /^\d{4}-\d{2}-\d{2}\.json$/.test(n))
      .map((n) => n.slice(0, 10))
      .sort()
      .reverse();
  } catch {
    return [];
  }
}

function toRow(v: unknown): IncomeListRow | null {
  const r = rec(v);
  const symbol = str(r["symbol"]);
  const spot = num(r["spot"]);
  const strike = num(r["strike"]);
  const delta = num(r["delta"]);
  if (symbol === null || spot === null || strike === null || delta === null) return null;
  const earnings = str(r["earnings"]) as IncomeEarnings | null;
  return {
    symbol,
    type: str(r["type"]),
    spot,
    expiry: str(r["expiry"]) ?? "",
    dte: num(r["dte"]) ?? 0,
    window: str(r["window"]),
    strike,
    delta,
    iv: num(r["iv"]) ?? 0,
    bid: num(r["bid"]),
    ask: num(r["ask"]),
    mid: num(r["mid"]) ?? 0,
    working: num(r["working"]),
    premium: num(r["premium"]),
    returnFraction: num(r["return"]),
    annualFraction: num(r["annual"]),
    distanceFraction: num(r["distance"]) ?? 0,
    openInterest: num(r["open_interest"]),
    volume: num(r["volume"]),
    passes: bool(r["passes"]),
    quiet: bool(r["quiet"]),
    // An unknown spelling is unknown, never clear.
    earnings: earnings !== null && EARNINGS.includes(earnings) ? earnings : "unknown",
    earningsDate: str(r["earnings_date"]),
    earningsEstimated: bool(r["earnings_estimated"]),
    exDividendInTrade: bool(r["ex_dividend_in_trade"]),
    leveraged: str(r["leveraged"]),
  };
}

export function readIncomeList(config: ConsoleConfig, list: IncomeList, dayParam: string | null): IncomeListPayload {
  const dir = path.join(config.paths.technicalsDir, "income-lists");
  const all = days(dir);
  const day = dayParam !== null && DAY.test(dayParam) && all.includes(dayParam) ? dayParam : (all[0] ?? null);
  const empty: IncomeListPayload = {
    list,
    day: null,
    days: all,
    builtAt: null,
    chainTakenAt: null,
    chainComplete: null,
    chainProblem: null,
    names: 0,
    dropped: {},
    targetDelta: null,
    rows: [],
  };
  if (day === null) return empty;
  const doc = readJson(path.join(dir, `${day}.json`));
  if (doc === null) return { ...empty, day };
  const rule = rec(doc["rule"]);
  const dropped: Record<string, number> = {};
  for (const [k, v] of Object.entries(rec(rec(doc["dropped"])[list]))) {
    const n = num(v);
    if (n !== null) dropped[k] = n;
  }
  const rawRows = rec(doc["lists"])[list];
  return {
    list,
    day,
    days: all,
    builtAt: str(doc["built_at"]),
    chainTakenAt: str(doc["chain_taken_at"]),
    chainComplete: bool(doc["chain_complete"]),
    chainProblem: str(doc["chain_problem"]),
    names: num(doc["names"]) ?? 0,
    dropped,
    targetDelta: num(rule[list === "short-puts" ? "short_put_delta" : "covered_call_delta"]),
    rows: (Array.isArray(rawRows) ? rawRows : []).map(toRow).filter((r): r is IncomeListRow => r !== null),
  };
}
