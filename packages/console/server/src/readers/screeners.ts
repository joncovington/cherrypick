/**
 * The income screener lists, joined for the console's Screeners page (2026-10-16).
 *
 * Every figure is another package's answer, read and joined, never re-derived:
 * - the list rows as the evening capture saved them (`market-report/vendor-screeners/<day>/`);
 * - our rule's strike for each row, from `packages/technicals` `score-screeners`
 *   (`technicals/screeners/<day>.json`, its `picks`), so the console holds no copy of the rule;
 * - each leg's liquidity verdict and the listed-or-cycle recommendation, from
 *   `fetch_screener_greeks.py measure` / `rebar` (`market-report/screener-greeks/legs/`), taken in
 *   regular hours the session after the list -- the bars are applied there, not here;
 * - our options-tradable verdict (`market-report/liquidity/verdicts.json`);
 * - the broker's expected report date (`market-report/iv/tastytrade.json`), else the list's own;
 * - each symbol's security type (`market-report/vendor-securities/<day>.json`), so a fund -- which
 *   never announces -- reads `none` rather than an unknown date.
 *
 * Two things ARE worked out here, both the user's calendar rather than a measurement: the cycle
 * expiry (the standard monthly 45-60 days out, else 30-60, as `fetch_screener_greeks.cycle_expiry`
 * picks it; the reading's own monthly leg wins where there is one) and whether an announcement falls
 * inside the trade (the list day through the expiry of the trade the row stands for, inclusive -- the
 * listed one or the cycle monthly, the later of the two until a reading says which; a date that does
 * not reach that far is `unknown`, never `clear`).
 */

import fs from "node:fs";
import path from "node:path";
import type {
  EarningsInCycle,
  ScreenerFidelityPayload,
  ScreenerFidelitySession,
  ScreenerLeg,
  ScreenerList,
  ScreenerPayload,
  ScreenerRate,
  ScreenerRow,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str } from "./db.js";

export const SCREENER_LISTS: readonly ScreenerList[] = ["short-puts", "covered-calls", "credit-spreads"];
const BODY_KEY: Record<ScreenerList, string> = {
  "short-puts": "shortPuts",
  "covered-calls": "coveredCalls",
  "credit-spreads": "creditSpreads",
};
const DAY = /^\d{4}-\d{2}-\d{2}$/;

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function list(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function readJson(file: string): Record<string, unknown> | null {
  try {
    return rec(JSON.parse(fs.readFileSync(file, "utf-8")));
  } catch {
    return null;
  }
}

export function marketReportDir(config: ConsoleConfig): string {
  return config.paths.marketReportDir ?? path.join(path.dirname(config.paths.technicalsDir), "market-report");
}

/** "60.25%" -> 60.25; "∞", "" or anything else -> null. */
function pct(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v !== "string") return null;
  const n = Number(v.trim().replace(/%$/, ""));
  return Number.isFinite(n) ? n : null;
}

/** "11/13/2026" (optionally followed by more, as a spread's "10/27/2026 (13) AM") -> "2026-11-13". */
function mdy(v: unknown): string | null {
  const m = typeof v === "string" ? /^(\d{2})\/(\d{2})\/(\d{4})/.exec(v.trim()) : null;
  return m ? `${m[3]}-${m[1]}-${m[2]}` : null;
}

function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000);
}

function isMonthly(iso: string): boolean {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.getUTCDay() === 5 && d.getUTCDate() >= 15 && d.getUTCDate() <= 21;
}

/** The standard monthly the user trades from `day`: the latest third Friday 45-60 days out, else
 *  30-60; null when neither range holds one (monthlies are 28 or 35 days apart). */
export function cycleExpiry(day: string): string | null {
  const fridays: string[] = [];
  const start = new Date(`${day}T00:00:00Z`);
  for (let i = 30; i <= 60; i++) {
    const d = new Date(start.getTime() + i * 86_400_000).toISOString().slice(0, 10);
    if (isMonthly(d)) fridays.push(d);
  }
  for (const [lo, hi] of [
    [45, 60],
    [30, 60],
  ] as const) {
    const inside = fridays.filter((f) => daysBetween(day, f) >= lo && daysBetween(day, f) <= hi);
    if (inside.length > 0) return inside[inside.length - 1]!;
  }
  return null;
}

/** In cycle when any known date falls in [day, expiry]; clear only when a known date lies past the
 *  expiry and none inside; otherwise unknown. */
export function earningsState(dates: Array<string | null>, day: string, expiry: string | null): EarningsInCycle {
  if (expiry === null) return "unknown";
  const ahead = dates.filter((d): d is string => d !== null && d >= day);
  if (ahead.some((d) => d <= expiry)) return "in-cycle";
  if (ahead.some((d) => d > expiry)) return "clear";
  return "unknown";
}

export function screenerDays(config: ConsoleConfig): string[] {
  try {
    return fs
      .readdirSync(path.join(marketReportDir(config), "vendor-screeners"), { withFileTypes: true })
      .filter((e) => e.isDirectory() && DAY.test(e.name))
      .map((e) => e.name)
      .sort()
      .reverse();
  } catch {
    return [];
  }
}

/** The latest leg reading made of `day`'s lists, keyed `<list>|<symbol>`. Readings sit under the
 *  date they were TAKEN (the next session), naming the list day they read. */
function readLegs(root: string, day: string): { at: string | null; rows: Map<string, Record<string, unknown>> } {
  const dir = path.join(root, "screener-greeks", "legs");
  let files: string[] = [];
  try {
    for (const d of fs.readdirSync(dir).filter((n) => DAY.test(n) && n >= day)) {
      for (const f of fs.readdirSync(path.join(dir, d)).filter((n) => n.endsWith(".json"))) files.push(path.join(dir, d, f));
    }
  } catch {
    files = [];
  }
  for (const file of files.sort().reverse()) {
    const doc = readJson(file);
    if (doc === null || doc["list_date"] !== day) continue;
    const rows = new Map<string, Record<string, unknown>>();
    for (const raw of list(doc["rows"])) {
      const r = rec(raw);
      const sym = str(r["symbol"]);
      const name = str(r["list"]);
      if (sym !== null && name !== null) rows.set(`${name}|${sym}`, r);
    }
    return { at: str(doc["measured_at"]), rows };
  }
  return { at: null, rows: new Map() };
}

function leg(raw: unknown): ScreenerLeg | null {
  const l = rec(raw);
  const q = rec(l["quote"]);
  const strike = num(l["strike"]);
  const expiry = str(l["expiry"]);
  if (strike === null || expiry === null) return null;
  return {
    role: l["role"] === "long" ? "long" : "short",
    right: l["right"] === "C" ? "C" : "P",
    expiry,
    strike,
    bid: num(q["bid"]),
    ask: num(q["ask"]),
    openInterest: num(q["open_interest"]),
    volume: num(q["volume"]),
    passes: typeof l["passes"] === "boolean" ? l["passes"] : null,
  };
}

/** The broker's report dates from the latest IV-rank reading on or before `day`. */
function brokerEarnings(root: string, day: string): Map<string, { date: string | null; estimated: boolean | null }> {
  const out = new Map<string, { date: string | null; estimated: boolean | null }>();
  const days = rec(readJson(path.join(root, "iv", "tastytrade.json"))?.["days"]);
  const at = Object.keys(days)
    .filter((d) => d <= day)
    .sort()
    .pop();
  if (at === undefined) return out;
  for (const [sym, raw] of Object.entries(rec(days[at]))) {
    const r = rec(raw);
    out.set(sym, {
      date: str(r["earnings_date"]),
      estimated: typeof r["earnings_estimated"] === "boolean" ? r["earnings_estimated"] : null,
    });
  }
  return out;
}

/** Types that never hold an earnings announcement. Stocks and depository receipts do. */
const FUND_TYPES = new Set(["Exchange Traded Fund", "Exchange Traded Commodity", "Fund", "Index"]);

/** Each symbol's security type from the latest security list on or before `day` (else the earliest). */
function securityTypes(root: string, day: string): Map<string, string> {
  const out = new Map<string, string>();
  const dir = path.join(root, "vendor-securities");
  let names: string[] = [];
  try {
    names = fs.readdirSync(dir).filter((n) => /^\d{4}-\d{2}-\d{2}\.json$/.test(n)).sort();
  } catch {
    return out;
  }
  const pick = names.filter((n) => n.slice(0, 10) <= day).pop() ?? names[0];
  if (pick === undefined) return out;
  for (const raw of list(readJson(path.join(dir, pick))?.["rows"])) {
    const s = rec(raw);
    const sym = str(s["symbol"]);
    const type = str(s["type"]);
    if (sym !== null && type !== null) out.set(sym, type);
  }
  return out;
}

function verdicts(root: string): Map<string, boolean> {
  const out = new Map<string, boolean>();
  for (const [sym, raw] of Object.entries(rec(readJson(path.join(root, "liquidity", "verdicts.json"))?.["names"]))) {
    // `technicals liquidity`'s two verdicts. A name it never judged is absent (null on the row).
    const v = rec(raw)["verdict"];
    if (v === "tradable" || v === "illiquid") out.set(sym, v === "tradable");
  }
  return out;
}

export function readScreener(config: ConsoleConfig, which: ScreenerList, wanted?: string | null): ScreenerPayload {
  const root = marketReportDir(config);
  const days = screenerDays(config);
  const day = wanted && days.includes(wanted) ? wanted : (days[0] ?? null);
  const empty: ScreenerPayload = { list: which, day, days, createdAt: null, pricesAt: null, scored: false, rows: [] };
  if (day === null) return empty;
  const doc = readJson(path.join(root, "vendor-screeners", day, `${which}.json`));
  if (doc === null) return empty;
  const body = rec(doc["body"]);
  const score = readJson(path.join(config.paths.technicalsDir, "screeners", `${day}.json`));
  const picks = rec(rec(score?.[which])["picks"]);
  const readings = readLegs(root, day);
  const earnings = brokerEarnings(root, day);
  const liquid = verdicts(root);
  const types = securityTypes(root, day);
  const defaultCycle = cycleExpiry(day);

  const rows: ScreenerRow[] = list(body[BODY_KEY[which]]).flatMap((raw) => {
    const r = rec(raw);
    const symbol = str(r["symbol"]);
    const expiry = mdy(r["expiry"]);
    if (symbol === null || expiry === null) return [];
    const spread = which === "credit-spreads";
    const right: "P" | "C" = which === "short-puts" ? "P" : which === "covered-calls" ? "C" : r["type"] === "Call" ? "C" : "P";
    const strike = rec(r["strike"]);
    const strikes = spread
      ? [num(strike["sell"]), num(strike["buy"])].filter((k): k is number => k !== null)
      : [num(r["strikePrice"])].filter((k): k is number => k !== null);
    const premium = rec(r["premium"]);
    const pick = picks[symbol];
    const ours = Array.isArray(pick) ? pick.map((k) => num(k)) : num(pick) !== null ? [num(pick)] : null;
    const reading = readings.rows.get(`${which}|${symbol.replace("/", ".")}`) ?? readings.rows.get(`${which}|${symbol}`);
    const cycleLegs = reading && Array.isArray(reading["monthly"]) ? reading["monthly"].map(leg).filter((l): l is ScreenerLeg => l !== null) : null;
    const cycle = cycleLegs?.[0]?.expiry ?? defaultCycle;
    const broker = earnings.get(symbol) ?? earnings.get(symbol.replace("/", "."));
    const listed = mdy(r["earningsDate"]);
    const dates = [broker?.date ?? null, listed];
    const nextKnown = dates.filter((d): d is string => d !== null && d >= day).sort()[0] ?? null;
    const rec_ = reading ? reading["recommendation"] : null;
    // Earnings are judged against the expiry of the trade the row stands for: the listed option
    // when it is the trade, the cycle monthly when that is; unread or with no liquid trade, the
    // later of the two, so a listed expiry spanning an announcement is never shown as clear.
    const later = cycle !== null && cycle > expiry ? cycle : expiry;
    const judged = rec_ === "vendor" ? expiry : rec_ === "monthly" ? cycle : later;
    return [
      {
        symbol,
        direction: spread && right === "C" ? "bearish" : "bullish",
        right,
        price: num(spread ? r["price"] : r["stockPrice"]),
        ivRank: pct(r["impliedVolatilityRank"]),
        liquid: liquid.get(symbol) ?? liquid.get(symbol.replace("/", ".")) ?? null,
        expiry,
        dte: num(r["daysToExpiry"]) ?? daysBetween(day, expiry),
        weekly: !isMonthly(expiry),
        strikes,
        mid: spread ? num(premium["value"]) : num(r["midPrice"]),
        bid: spread ? null : num(r["bidPrice"]),
        ask: spread ? null : num(r["askPrice"]),
        rawReturnPct: spread ? null : pct(r["rawReturn"]),
        annualisedPct: spread ? null : pct(r["annualizedReturn"]),
        distancePct: spread ? null : pct(r["distanceToStrike"]),
        width: spread ? num(premium["width"]) : null,
        creditPct: spread ? pct(premium["percentage"]) : null,
        ours,
        earningsDate: nextKnown,
        earningsSource: nextKnown === null ? null : nextKnown === broker?.date ? "broker" : "list",
        earningsEstimated: nextKnown !== null && nextKnown === broker?.date ? broker.estimated : null,
        cycleExpiry: cycle,
        earningsExpiry: judged,
        earnings: FUND_TYPES.has(types.get(symbol) ?? "") ? "none" : earningsState(dates, day, judged),
        readingAt: reading ? readings.at : null,
        listedLegs: reading ? list(reading["vendor"]).map(leg).filter((l): l is ScreenerLeg => l !== null) : [],
        cycleLegs,
        recommendation: rec_ === "vendor" ? "listed" : rec_ === "monthly" ? "cycle" : rec_ === "none" ? "none" : null,
        cyclePasses: reading && typeof reading["monthly_passes"] === "boolean" ? reading["monthly_passes"] : null,
      },
    ];
  });
  return {
    list: which,
    day,
    days,
    createdAt: str(body["created"]),
    pricesAt: str(body["pricesAt"]),
    scored: score !== null,
    rows,
  };
}

function rate(v: unknown): ScreenerRate {
  const r = rec(v);
  return { hit: num(r["hit"]) ?? 0, n: num(r["n"]) ?? 0 };
}

export function readScreenerFidelity(config: ConsoleConfig): ScreenerFidelityPayload {
  const dir = path.join(config.paths.technicalsDir, "screeners");
  let files: string[] = [];
  try {
    files = fs.readdirSync(dir).filter((n) => /^\d{4}-\d{2}-\d{2}\.json$/.test(n));
  } catch {
    files = [];
  }
  const sessions: ScreenerFidelitySession[] = files
    .sort()
    .reverse()
    .flatMap((f) => {
      const d = readJson(path.join(dir, f));
      if (d === null) return [];
      const sp = rec(d["short-puts"]);
      const cc = rec(d["covered-calls"]);
      const cs = rec(d["credit-spreads"]);
      return [
        {
          day: f.slice(0, 10),
          shortPuts: { monthly: rate(rec(sp["strike"])["monthly"]), weekly: rate(rec(sp["strike"])["weekly"]), expiry: rate(sp["expiry"]), returns: rate(sp["returns"]) },
          coveredCalls: { monthly: rate(rec(cc["strike"])["monthly"]), weekly: rate(rec(cc["strike"])["weekly"]), expiry: rate(cc["expiry"]), returns: rate(cc["returns"]) },
          creditSpreads: { expiry: rate(cs["expiry"]), short: rate(cs["short"]), long: rate(cs["long"]), floor: rate(cs["floor"]) },
        },
      ];
    });
  if (sessions.length === 0) return { sessions, totals: null };
  const sum = (pick: (s: ScreenerFidelitySession) => ScreenerRate): ScreenerRate =>
    sessions.reduce((t, s) => ({ hit: t.hit + pick(s).hit, n: t.n + pick(s).n }), { hit: 0, n: 0 });
  return {
    sessions,
    totals: {
      shortPuts: { monthly: sum((s) => s.shortPuts.monthly), weekly: sum((s) => s.shortPuts.weekly), expiry: sum((s) => s.shortPuts.expiry), returns: sum((s) => s.shortPuts.returns) },
      coveredCalls: { monthly: sum((s) => s.coveredCalls.monthly), weekly: sum((s) => s.coveredCalls.weekly), expiry: sum((s) => s.coveredCalls.expiry), returns: sum((s) => s.coveredCalls.returns) },
      creditSpreads: { expiry: sum((s) => s.creditSpreads.expiry), short: sum((s) => s.creditSpreads.short), long: sum((s) => s.creditSpreads.long), floor: sum((s) => s.creditSpreads.floor) },
    },
  };
}
