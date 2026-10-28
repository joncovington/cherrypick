/**
 * Our own short-put and covered-call lists: `packages/technicals` `income-lists` writes one file a
 * session (`technicals/income-lists/<session>.json`); this renames its rows and decides nothing. The
 * strike, delta, working price, return, liquidity verdict and earnings state are all the builder's.
 * A `*.trial.json` is never read.
 */
import fs from "node:fs";
import path from "node:path";
import type {
  CreditSpreadPayload,
  CreditSpreadPrice,
  CreditSpreadRow,
  CreditSpreadSelectionPick,
  CreditSpreadWidth,
  FairValueFields,
  IncomeEarnings,
  IncomeExDividend,
  IncomeList,
  IncomeListPayload,
  IncomeListRow,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str } from "./db.js";

export const INCOME_LISTS: readonly IncomeList[] = ["short-puts", "covered-calls"];
const DAY = /^\d{4}-\d{2}-\d{2}$/;
const EARNINGS: readonly IncomeEarnings[] = ["in-trade", "clear", "unknown", "none"];
const EX_DIVIDEND: readonly IncomeExDividend[] = ["in-trade", "after", "past", "unknown"];

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

const FAIR_STATES: readonly string[] = ["valued", "not-applicable", "no-statements", "refused", "unbuilt", "unlisted"];

/** The six fair-value fields a version-3 row carries; null for a row from before them. */
function fairOf(r: Record<string, unknown>): FairValueFields | null {
  if (!("fair_state" in r)) return null;
  const state = str(r["fair_state"]);
  return {
    fairValue: num(r["fair_value"]),
    fairLow: num(r["fair_low"]),
    fairHigh: num(r["fair_high"]),
    fairGap: num(r["fair_gap"]),
    fairState: state !== null && FAIR_STATES.includes(state) ? (state as FairValueFields["fairState"]) : null,
    fairFlags: Array.isArray(r["fair_flags"]) ? r["fair_flags"].filter((f): f is string => typeof f === "string") : [],
  };
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
    // A list built before the state existed has none: null, and the page reads the old flag.
    exDividend: EX_DIVIDEND.includes(str(r["ex_dividend"]) as IncomeExDividend) ? (str(r["ex_dividend"]) as IncomeExDividend) : null,
    exDividendDate: str(r["ex_dividend_date"]),
    leveraged: str(r["leveraged"]),
    fit: null, // joined from manage's evaluation in readIncomeList
    fair: fairOf(r),
  };
}

/** manage's account judgement of the Screeners trades for `day` (`candidates.screen_fit`), from that
 *  session's evaluation; null when there is none. The lists stay account-blind: the rule that decides
 *  a fit is manage's, and this only looks it up. */
function screenFit(config: ConsoleConfig, day: string) {
  const dir = config.paths.manageDir;
  const doc = dir ? readJson(path.join(dir, `eval-${day}.json`)) : null;
  const acct = rec(Array.isArray(doc?.["accounts"]) ? (doc!["accounts"] as unknown[])[0] : null);
  const cands = rec(rec(acct["portfolio"])["candidates"]);
  if (!("screen_fit" in cands)) return null;
  return {
    basis: { session: day, availableBp: num(cands["available_bp"]), basis: str(cands["account_basis"]) },
    fit: rec(cands["screen_fit"]),
  };
}

function fitOf(list: IncomeList, row: IncomeListRow, fit: Record<string, unknown>): boolean | null {
  const name = rec(fit[row.symbol]);
  if (Object.keys(name).length === 0) return null;
  if (list === "covered-calls") {
    const n = num(name["covered_call"]);
    return n === null ? null : n >= 1;
  }
  // Keyed as manage writes a strike (`f"{strike:g}"`): 40 -> "40", 37.5 -> "37.5".
  const f = rec(rec(name["short_put"])[String(row.strike)]);
  return bool(f["fits"]);
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
    fitBasis: null,
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
  const judged = screenFit(config, day);
  const rows = (Array.isArray(rawRows) ? rawRows : []).map(toRow).filter((r): r is IncomeListRow => r !== null);
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
    rows: judged === null ? rows : rows.map((r) => ({ ...r, fit: fitOf(list, r, judged.fit) })),
    fitBasis: judged?.basis ?? null,
  };
}

// --- our credit-spread list (income-lists version 2, 2026-10-24) -------------------------------

export const CREDIT_SPREADS = "credit-spreads";

function toPrice(v: unknown): CreditSpreadPrice | null {
  const p = rec(v);
  const long = num(p["long"]);
  const width = num(p["width"]);
  const credit = num(p["credit"]);
  if (long === null || width === null || credit === null) return null;
  return {
    long,
    longDelta: num(p["long_delta"]) ?? 0,
    width,
    credit,
    creditMid: num(p["credit_mid"]) ?? credit,
    creditWidth: num(p["credit_width"]),
    maxLoss: num(p["max_loss"]) ?? width - credit,
    creditMaxLoss: num(p["credit_max_loss"]),
    passes: bool(p["passes"]),
  };
}

function toWidth(v: unknown): CreditSpreadWidth | null {
  const w = rec(v);
  const unit = str(w["unit"]);
  const size = num(w["size"]);
  if ((unit !== "strikes" && unit !== "move") || size === null) return null;
  // The builder writes a width's figures flat beside its unit; one the chain does not reach has none.
  return { unit, size, price: toPrice(w) };
}

function toShort(v: unknown): CreditSpreadSelectionPick["short"] | null {
  const s = rec(v);
  const strike = num(s["strike"]);
  if (strike === null) return null;
  return { strike, delta: num(s["delta"]) ?? 0, iv: num(s["iv"]) ?? 0, bid: num(s["bid"]), ask: num(s["ask"]) };
}

function toSelections(v: unknown): Record<string, CreditSpreadSelectionPick | null> | undefined {
  if (v === undefined || v === null) return undefined;
  const out: Record<string, CreditSpreadSelectionPick | null> = {};
  for (const [k, x] of Object.entries(rec(v))) {
    const short = toShort(rec(x)["short"]);
    const price = toPrice(x);
    out[k] = short !== null && price !== null ? { short, price } : null;
  }
  return out;
}

function toSpread(v: unknown): CreditSpreadRow | null {
  const r = rec(v);
  const symbol = str(r["symbol"]);
  const spot = num(r["spot"]);
  const spread = str(r["spread"]);
  const short = rec(r["short"]);
  const shortStrike = num(short["strike"]);
  if (symbol === null || spot === null || (spread !== "put" && spread !== "call") || shortStrike === null) return null;
  const earnings = str(r["earnings"]) as IncomeEarnings | null;
  const ex = str(r["ex_dividend"]) as IncomeExDividend | null;
  const signals = (Array.isArray(r["signals"]) ? r["signals"] : [])
    .map((s) => rec(s))
    .flatMap((s) => {
      const setup = str(s["setup"]);
      const date = str(s["date"]);
      return setup === null || date === null ? [] : [{ setup, date, ago: num(s["ago"]) ?? 0 }];
    });
  return {
    symbol,
    type: str(r["type"]),
    spread,
    setups: (Array.isArray(r["setups"]) ? r["setups"] : []).map(str).filter((s): s is string => s !== null),
    signals,
    conflict: r["conflict"] === true,
    spot,
    expiry: str(r["expiry"]) ?? "",
    dte: num(r["dte"]) ?? 0,
    move: num(r["move"]),
    short: {
      strike: shortStrike,
      delta: num(short["delta"]) ?? 0,
      iv: num(short["iv"]) ?? 0,
      bid: num(short["bid"]),
      ask: num(short["ask"]),
    },
    pick: toPrice(r["pick"]),
    window: r["window"] === "45-60" ? "45-60" : "30-45",
    selections: toSelections(r["selections"]),
    widths: (Array.isArray(r["widths"]) ? r["widths"] : []).map(toWidth).filter((w): w is CreditSpreadWidth => w !== null),
    // An unknown spelling is unknown, never clear.
    earnings: earnings !== null && EARNINGS.includes(earnings) ? earnings : "unknown",
    earningsDate: str(r["earnings_date"]),
    earningsEstimated: bool(r["earnings_estimated"]),
    exDividend: spread === "call" ? (ex !== null && EX_DIVIDEND.includes(ex) ? ex : "unknown") : null,
    exDividendDate: str(r["ex_dividend_date"]),
    leveraged: str(r["leveraged"]),
    fair: fairOf(r),
  };
}

/** The credit-spread list for `dayParam` (else the newest session). A session built before the list
 *  existed, or without the history store, has no `credit-spreads` key: `built` false, never an empty
 *  list that would read as "no signals today". */
export function readCreditSpreadList(config: ConsoleConfig, dayParam: string | null): CreditSpreadPayload {
  const dir = path.join(config.paths.technicalsDir, "income-lists");
  const all = days(dir);
  const day = dayParam !== null && DAY.test(dayParam) && all.includes(dayParam) ? dayParam : (all[0] ?? null);
  const empty: CreditSpreadPayload = {
    day: null,
    days: all,
    builtAt: null,
    chainTakenAt: null,
    chainComplete: null,
    chainProblem: null,
    names: 0,
    dropped: {},
    built: false,
    rule: { shortDelta: null, creditShare: null, recentSessions: null, tested: null, selections: [] },
    rows: [],
  };
  if (day === null) return empty;
  const doc = readJson(path.join(dir, `${day}.json`));
  if (doc === null) return { ...empty, day };
  const lists = rec(doc["lists"]);
  const rule = rec(rec(doc["rule"])["credit_spreads"]);
  const share = Array.isArray(rule["credit_share"]) ? (rule["credit_share"] as unknown[]).map(num) : [];
  const [lo, hi] = [share[0] ?? null, share[1] ?? null];
  const dropped: Record<string, number> = {};
  for (const [k, v] of Object.entries(rec(rec(doc["dropped"])[CREDIT_SPREADS]))) {
    const n = num(v);
    if (n !== null) dropped[k] = n;
  }
  const raw = lists[CREDIT_SPREADS];
  return {
    day,
    days: all,
    builtAt: str(doc["built_at"]),
    chainTakenAt: str(doc["chain_taken_at"]),
    chainComplete: bool(doc["chain_complete"]),
    chainProblem: str(doc["chain_problem"]),
    names: num(doc["names"]) ?? 0,
    dropped,
    built: Array.isArray(raw),
    rule: {
      shortDelta: num(rule["short_delta"]),
      creditShare: share.length === 2 && lo !== null && hi !== null ? [lo, hi] : null,
      recentSessions: num(rule["recent_sessions"]),
      tested: str(rule["tested"]),
      selections: Object.entries(rec(rule["selections"])).map(([id, s]) => ({
        id,
        label: str(rec(s)["label"]) ?? id,
        rule: str(rec(s)["rule"]) ?? "",
      })),
    },
    rows: (Array.isArray(raw) ? raw : []).map(toSpread).filter((r): r is CreditSpreadRow => r !== null),
  };
}
