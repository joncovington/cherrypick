/**
 * The income screener lists, joined for the console's Screeners page (2026-10-16).
 *
 * Every figure is another package's answer, read and joined, never re-derived:
 * - the list rows as the evening capture saved them (`market-report/vendor-screeners/<day>/`);
 * - our rule's strike for each row, from `packages/technicals` `score-screeners`
 *   (`technicals/screeners/<day>.json`, its `picks`), so the console holds no copy of the rule;
 * - each leg's liquidity verdict, the listed-or-cycle recommendation and each side's price (mid,
 *   working -- halfway to the natural fill -- and natural, a spread judged as one order), from
 *   `fetch_screener_greeks.py measure` / `rebar` (`market-report/screener-greeks/legs/`), taken in
 *   regular hours the session after the list -- the bars are applied there, not here;
 * - our options-tradable verdict (`market-report/liquidity/verdicts.json`);
 * - the broker's expected report date (`market-report/iv/tastytrade.json`), else the list's own;
 * - each symbol's security type from the current security list (`market-report/securities/current.json`
 *   names it: the vendor's or the public one), so a fund -- which never announces -- reads `none`
 *   rather than an unknown date.
 *
 * Two things ARE worked out here, both the user's calendar rather than a measurement: the cycle
 * expiry (the standard monthly 45-60 days out, else 30-60, as `fetch_screener_greeks.cycle_expiry`
 * picks it; the reading's own monthly leg wins where there is one) and whether an announcement falls
 * inside the trade (the day of the reading the trade is priced from -- the list day when there is no
 * reading yet -- through the expiry of the trade the row stands for, inclusive, a before-the-open
 * report on that day already behind it -- the
 * listed one or the cycle monthly, the later of the two until a reading says which; a date that does
 * not reach that far is `unknown`, never `clear`).
 */

import fs from "node:fs";
import path from "node:path";
import type {
  EarningsInCycle,
  ScreenerLeg,
  ScreenerList,
  ScreenerPayload,
  ScreenerRow,
  ScreenerTrade,
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

/** A report before the open: the broker spells it BTO, most calendars BMO (`cherrypick.manage`'s
 *  `BEFORE_OPEN`, mirrored). */
const BEFORE_OPEN = new Set(["BTO", "BMO"]);

/** Whether a report on `date`, at `time`, has already happened by an afternoon reading on `day`: any
 *  earlier day, or `day` itself when it was before the open. An untimed report on `day` has not --
 *  the safe reading keeps it in the trade. */
export function reportedBy(date: string, time: string | null | undefined, day: string): boolean {
  return date < day || (date === day && BEFORE_OPEN.has(String(time ?? "").toUpperCase()));
}

/** The New York calendar day of an ISO instant ("2026-10-21T18:30:11Z" -> "2026-10-21"); null when unreadable. */
export function newYorkDay(iso: string | null): string | null {
  if (iso === null) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(t);
}

/** In cycle when any known date falls in [day, expiry]; clear only when a known date lies past the
 *  expiry and none inside; otherwise unknown. `done` is a date already reported by the reading (a
 *  before-the-open report on `day` itself), which is behind the trade, not in it. */
export function earningsState(
  dates: Array<string | null>,
  day: string,
  expiry: string | null,
  done: string | null = null,
): EarningsInCycle {
  if (expiry === null) return "unknown";
  const ahead = dates.filter((d): d is string => d !== null && d >= day && d !== done);
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
    quiet: typeof l["quiet"] === "boolean" ? l["quiet"] : null,
  };
}

/**
 * The recommended trade priced from the reading's own figures (`vendor_prices` / `monthly_prices`,
 * written by `fetch_screener_greeks.py` where the bars are applied). The one thing worked out here is
 * arithmetic the list itself states its returns in: premium over the share price, x 365 / days.
 * `day` is the reading's own day (the session after the list), so the days to expiry are the trade's;
 * `spot` is the share price that afternoon, against which a short already in the money is marked.
 */
export function tradePrice(
  reading: Record<string, unknown> | undefined,
  listedExpiry: string,
  cycleLegs: ScreenerLeg[] | null,
  day: string,
  price: number | null,
  width: number | null,
  spot: number | null = null,
): ScreenerTrade | null {
  if (!reading) return null;
  const which = reading["recommendation"];
  if (which !== "vendor" && which !== "monthly") return null;
  const p = rec(reading[which === "vendor" ? "vendor_prices" : "monthly_prices"]);
  const mid = num(p["mid"]);
  const working = num(p["working"]);
  const natural = num(p["natural"]);
  const cost = num(p["cost"]);
  if (mid === null || working === null || natural === null || cost === null) return null;
  const expiry = which === "vendor" ? listedExpiry : (cycleLegs?.[0]?.expiry ?? null);
  if (expiry === null) return null;
  // Days from the reading the trade is priced at, never from the list day before it.
  const dte = daysBetween(day, expiry);
  const annual = (premium: number): number | null =>
    width === null && price !== null && price > 0 && dte > 0 ? (premium / price) * (365 / dte) * 100 : null;
  const legs = which === "vendor" ? list(reading["vendor"]).map(leg) : (cycleLegs ?? []);
  const short = legs.find((l) => l !== null && l.role === "short") ?? null;
  const itm =
    short === null || spot === null ? null : short.right === "C" ? short.strike < spot : short.strike > spot;
  return {
    side: which === "vendor" ? "listed" : "cycle",
    expiry,
    dte,
    spot,
    shortInTheMoney: itm,
    mid,
    working,
    natural,
    cost,
    workingAnnualisedPct: annual(working),
    naturalAnnualisedPct: annual(natural),
    workingCreditPct: width !== null && width > 0 ? (working / width) * 100 : null,
  };
}

/** Whether the list's own quote can back the mid it prices its return at. */
export function listedQuote(bid: number | null, ask: number | null): "no-bid" | "wide" | null {
  if (ask === null) return null;
  if (bid === null || bid <= 0) return "no-bid";
  const mid = (bid + ask) / 2;
  return ask - bid > mid ? "wide" : null;
}

/** The broker's report dates from the latest IV-rank reading on or before `day`. */
function brokerEarnings(
  root: string,
  day: string,
): Map<string, { date: string | null; estimated: boolean | null; time: string | null }> {
  const out = new Map<string, { date: string | null; estimated: boolean | null; time: string | null }>();
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
      time: str(r["earnings_time"]),
    });
  }
  return out;
}

/** Each name's share price in the afternoon income reading of `day` (`screener-greeks/income/<day>.json`,
 *  14:45 ET), and when it was taken; empty when there is none. */
function afternoonSpots(root: string, day: string): { at: string | null; spots: Map<string, number> } {
  const doc = readJson(path.join(root, "screener-greeks", "income", `${day}.json`));
  const spots = new Map<string, number>();
  for (const [sym, raw] of Object.entries(rec(doc?.["symbols"]))) {
    const s = num(rec(raw)["spot"]);
    if (s !== null && s > 0) spots.set(sym, s);
  }
  return { at: str(doc?.["taken_at"]), spots };
}

/** Types that never hold an earnings announcement. Stocks and depository receipts do. */
const FUND_TYPES = new Set(["Exchange Traded Fund", "Exchange Traded Commodity", "Fund", "Index"]);

function listDays(dir: string): string[] {
  try {
    return fs.readdirSync(dir).filter((n) => /^\d{4}-\d{2}-\d{2}\.json$/.test(n)).sort();
  } catch {
    return [];
  }
}

/**
 * Each symbol's security type from the latest security list on or before `day` (else the earliest).
 * Which list -- the vendor's or the public one -- is `technicals securities`' decision, named in
 * `securities/current.json`; this follows it and never re-decides. No pointer, or nothing in the
 * list it names, reads the other.
 */
function securityTypes(root: string, day: string): Map<string, string> {
  const out = new Map<string, string>();
  const source = str(rec(readJson(path.join(root, "securities", "current.json")))["source"]);
  const order = source === "public" ? ["securities", "vendor-securities"] : ["vendor-securities", "securities"];
  const found = order.map((d) => ({ dir: path.join(root, d), names: listDays(path.join(root, d)) })).find((x) => x.names.length > 0);
  if (found === undefined) return out;
  const { dir, names } = found;
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
  const empty: ScreenerPayload = {
    list: which,
    day,
    days,
    createdAt: null,
    pricesAt: null,
    scored: false,
    tradeDay: null,
    spotAt: null,
    rows: [],
  };
  if (day === null) return empty;
  const doc = readJson(path.join(root, "vendor-screeners", day, `${which}.json`));
  if (doc === null) return empty;
  const body = rec(doc["body"]);
  const score = readJson(path.join(config.paths.technicalsDir, "screeners", `${day}.json`));
  const picks = rec(rec(score?.[which])["picks"]);
  const readings = readLegs(root, day);
  // The trade is priced from the regular-hours reading the session after the list: its days to
  // expiry, its earnings and its spot are judged from THAT day, not the list day before it.
  const tradeDay = newYorkDay(readings.at) ?? day;
  const earnings = brokerEarnings(root, tradeDay);
  const liquid = verdicts(root);
  const types = securityTypes(root, day);
  const defaultCycle = cycleExpiry(day);
  const afternoon = readings.at === null ? { at: null, spots: new Map<string, number>() } : afternoonSpots(root, tradeDay);

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
    // A report before the open on the reading's own day has happened by the afternoon reading.
    const done = broker?.date && broker.date === tradeDay && reportedBy(broker.date, broker.time, tradeDay) ? broker.date : null;
    const nextKnown = dates.filter((d): d is string => d !== null && d >= tradeDay && d !== done).sort()[0] ?? null;
    const rec_ = reading ? reading["recommendation"] : null;
    // Earnings are judged against the expiry of the trade the row stands for: the listed option
    // when it is the trade, the cycle monthly when that is; unread or with no liquid trade, the
    // later of the two, so a listed expiry spanning an announcement is never shown as clear.
    const later = cycle !== null && cycle > expiry ? cycle : expiry;
    const judged = rec_ === "vendor" ? expiry : rec_ === "monthly" ? cycle : later;
    const dte = num(r["daysToExpiry"]) ?? daysBetween(day, expiry);
    const price = num(spread ? r["price"] : r["stockPrice"]);
    const width = spread ? num(premium["width"]) : null;
    return [
      {
        symbol,
        direction: spread && right === "C" ? "bearish" : "bullish",
        right,
        price,
        ivRank: pct(r["impliedVolatilityRank"]),
        liquid: liquid.get(symbol) ?? liquid.get(symbol.replace("/", ".")) ?? null,
        expiry,
        dte,
        weekly: !isMonthly(expiry),
        strikes,
        mid: spread ? num(premium["value"]) : num(r["midPrice"]),
        bid: spread ? null : num(r["bidPrice"]),
        ask: spread ? null : num(r["askPrice"]),
        rawReturnPct: spread ? null : pct(r["rawReturn"]),
        annualisedPct: spread ? null : pct(r["annualizedReturn"]),
        listedQuote: spread ? null : listedQuote(num(r["bidPrice"]), num(r["askPrice"])),
        distancePct: spread ? null : pct(r["distanceToStrike"]),
        width,
        creditPct: spread ? pct(premium["percentage"]) : null,
        ours,
        earningsDate: nextKnown,
        earningsSource: nextKnown === null ? null : nextKnown === broker?.date ? "broker" : "list",
        earningsEstimated: nextKnown !== null && nextKnown === broker?.date ? broker.estimated : null,
        cycleExpiry: cycle,
        earningsExpiry: judged,
        earnings: FUND_TYPES.has(types.get(symbol) ?? "") ? "none" : earningsState(dates, tradeDay, judged, done),
        earningsFrom: tradeDay,
        readingAt: reading ? readings.at : null,
        listedLegs: reading ? list(reading["vendor"]).map(leg).filter((l): l is ScreenerLeg => l !== null) : [],
        cycleLegs,
        recommendation: rec_ === "vendor" ? "listed" : rec_ === "monthly" ? "cycle" : rec_ === "none" ? "none" : null,
        cyclePasses: reading && typeof reading["monthly_passes"] === "boolean" ? reading["monthly_passes"] : null,
        trade: tradePrice(
          reading,
          expiry,
          cycleLegs,
          tradeDay,
          price,
          width,
          afternoon.spots.get(symbol.replace("/", ".")) ?? afternoon.spots.get(symbol) ?? null,
        ),
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
    tradeDay: readings.at === null ? null : tradeDay,
    spotAt: afternoon.at,
    rows,
  };
}
