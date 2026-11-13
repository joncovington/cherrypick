import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";
import type { ManageLiveAccount, ManageLivePayload, ManageLiveTrade } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { accountName } from "./accountName.js";
import { num } from "./db.js";
import { listSessions } from "./manage.js";

/**
 * The newest manage evaluation re-marked from the stream cache (2026-10-22), so Live → positions and
 * Live → portfolio move between evaluations. packages/manage declares every held leg, its underlying
 * and the benchmark in its own stream request; this reads what the streamer wrote for them.
 *
 * It re-prices and re-sums, and judges nothing: the basis (cost, fees, the capture denominator), the
 * exit levels and the betas are the evaluation's. A trade or a book figure is null unless every input
 * is fresh, so a part-marked trade never reads as a whole one.
 */

const MAX_AGE_S = 120;
/**
 * Out of the session (2026-11-05) a stock or ETF option stops quoting at its close, so its last quote IS
 * its closing mark and stays its mark until the open; only the index options keep quoting overnight.
 * Then a quote up to this old is a mark (a weekend and a holiday), and the payload says the marks are
 * the close. The session is closed only when a fresh watchdog says so AND the New York clock is outside
 * a weekday's 09:30-16:00; anything else keeps the 2-minute rule, so neither a missing watchdog nor one
 * a cycle behind the open lets an old quote pass as live in the session.
 */
const CLOSED_MAX_AGE_S = 4 * 86_400;
/** The watchdog writes every 10 minutes: three of its cycles. */
const WATCHDOG_FRESH_S = 1_800;

/** Weekday and minutes since midnight on the New York clock. */
function nyClock(now: number): { weekday: string; minutes: number } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(new Date(now))
      .map((p) => [p.type, p.value]),
  );
  return { weekday: String(parts["weekday"]), minutes: Number(parts["hour"]) * 60 + Number(parts["minute"]) };
}

/** True only when a fresh watchdog says the market is out of its session and the clock agrees. */
export function sessionClosed(config: ConsoleConfig, now: number): boolean {
  const p = config.paths.watchdogLast;
  if (!p) return false;
  const ny = nyClock(now);
  const weekday = !["Sat", "Sun"].includes(ny.weekday);
  if (weekday && ny.minutes >= 9 * 60 + 30 && ny.minutes < 16 * 60) return false;
  try {
    const raw = rec(JSON.parse(fs.readFileSync(p, "utf-8")));
    const at = typeof raw["ts"] === "string" ? Date.parse(raw["ts"]) : Number.NaN;
    return Number.isFinite(at) && now - at <= WATCHDOG_FRESH_S * 1000 && raw["in_session"] === false;
  } catch {
    return false;
  }
}

type Rec = Record<string, unknown>;

function rec(v: unknown): Rec {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : {};
}

function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

/** OCC 2010 → DXLink streamer symbol, as `cherrypick.core.streamcache.occ_to_streamer_symbol` keys
 *  the cache ("GLW   261120P00112500" → ".GLW261120P112.5"). "" for anything that is not an option. */
export function occToStreamer(occ: string): string {
  const root = occ.slice(0, 6).trim();
  const m = /^(\d{6})([CP])(\d{5})(\d{3})/.exec(occ.slice(6));
  if (!root || !m) return "";
  const [, exp, right, dollars, thousandths] = m;
  let out = `.${root}${exp}${right}${Number(dollars)}`;
  if (Number(thousandths)) out += String(Number(thousandths) / 1000).slice(1);
  return out;
}

export interface Fresh {
  mids: Map<string, number>;
  greeks: Map<string, { delta: number | null; theta: number | null }>;
  oldest: number | null;
}

/** The cache a broker login's own producer writes (`streamer.instances`): `<marketdata>/<login>/
 *  stream_cache.db`, beside the shared one. Main's is the shared cache itself. */
export function instanceCachePath(config: ConsoleConfig, login: string | null): string {
  if (!login || login === "main") return config.paths.streamCacheDb;
  return path.join(path.dirname(config.paths.streamCacheDb), login, "stream_cache.db");
}

export function readFresh(
  config: ConsoleConfig,
  symbols: string[],
  now: number,
  maxAgeS: number,
  cachePath: string = config.paths.streamCacheDb,
): Fresh {
  const out: Fresh = { mids: new Map(), greeks: new Map(), oldest: null };
  const p = cachePath;
  if (symbols.length === 0 || !fs.existsSync(p)) return out;
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    const cutoff = now / 1000 - maxAgeS;
    const marks = symbols.map(() => "?").join(",");
    const quotes = db
      .prepare(`SELECT symbol, bid, ask, mid, updated_at FROM stream_quotes WHERE symbol IN (${marks}) AND updated_at >= ?`)
      .all(...symbols, cutoff) as Rec[];
    for (const q of quotes) {
      const bid = num(q["bid"]);
      const ask = num(q["ask"]);
      const mid = num(q["mid"]) ?? (bid !== null && ask !== null ? (bid + ask) / 2 : null);
      // A one-sided or crossed book is no mark.
      if (mid === null || bid === null || ask === null || ask < bid) continue;
      out.mids.set(String(q["symbol"]), mid);
      const at = (num(q["updated_at"]) ?? 0) * 1000;
      out.oldest = out.oldest === null ? at : Math.min(out.oldest, at);
    }
    // An index has no book to quote (the streamer subscribes it on Trade only): its last print is its spot.
    const unquoted = symbols.filter((s) => !s.startsWith(".") && !out.mids.has(s));
    if (unquoted.length > 0) {
      const trades = db
        .prepare(
          `SELECT symbol, last FROM stream_trades WHERE symbol IN (${unquoted.map(() => "?").join(",")}) AND updated_at >= ?`,
        )
        .all(...unquoted, cutoff) as Rec[];
      for (const t of trades) {
        const last = num(t["last"]);
        if (last !== null && last > 0) out.mids.set(String(t["symbol"]), last);
      }
    }
    const greeks = db
      .prepare(`SELECT symbol, delta, theta FROM stream_greeks WHERE symbol IN (${marks}) AND updated_at >= ?`)
      .all(...symbols, cutoff) as Rec[];
    for (const g of greeks) out.greeks.set(String(g["symbol"]), { delta: num(g["delta"]), theta: num(g["theta"]) });
  } catch {
    /* an unreadable cache is no live marks, never an error page */
  } finally {
    db?.close();
  }
  return out;
}

/** A leg as this reader needs it: its cache key, and the dollars one unit of its mid is worth. */
interface Leg {
  key: string;
  option: boolean;
  quantity: number;
  /** value ÷ (mid × quantity) on the evaluation: 100 for an option, 1 for shares. */
  scale: number;
}

function legOf(raw: unknown): Leg | null {
  const l = rec(raw);
  const symbol = typeof l["symbol"] === "string" ? l["symbol"] : "";
  const quantity = num(l["quantity"]);
  if (!symbol || quantity === null || quantity === 0) return null;
  const option = typeof l["right"] === "string" && l["right"] !== "";
  const key = option ? occToStreamer(symbol) : symbol;
  if (!key) return null;
  const mid = num(l["mid"]);
  const value = num(l["value"]);
  const derived = mid && value !== null ? value / (mid * quantity) : null;
  // The evaluation's own multiplier where it can be read back; the standard one otherwise.
  const scale = derived !== null && Number.isFinite(derived) && derived > 0 ? Math.round(derived) : option ? 100 : 1;
  return { key, option, quantity, scale };
}

function liveTrade(t: Rec, fresh: Fresh): ManageLiveTrade {
  const legs = arr(t["legs"]).map(legOf);
  const quoted = legs.filter((l): l is Leg => l !== null && fresh.mids.has(l.key));
  const whole = legs.length > 0 && quoted.length === legs.length;
  const cost = num(t["cost"]);
  const value = whole ? quoted.reduce((s, l) => s + fresh.mids.get(l.key)! * l.quantity * l.scale, 0) : null;
  const pnl = value !== null && cost !== null ? value - cost : null;
  const fees = num(t["fees"]);
  // The capture denominator is the evaluation's (|cost| for a credit or debit, the max loss for a
  // trade judged on its risk), read back from its own pnl and capture.
  const evalPnl = num(t["pnl_gross"]);
  const evalCapture = num(t["capture"]);
  const den = evalPnl !== null && evalCapture ? evalPnl / evalCapture : cost !== null && Math.abs(cost) >= 1 ? Math.abs(cost) : null;
  const levels = rec(t["exit_levels"]);
  const tp = num(rec(levels["take_profit"])["close_value"]);
  const stop = num(rec(levels["stop"])["close_value"]);
  return {
    tradeId: (typeof t["trade_id"] === "string" ? t["trade_id"] : null) ?? String(t["underlying"] ?? "?"),
    value,
    pnlGross: pnl,
    pnlNet: pnl !== null && fees !== null ? pnl + fees : null,
    capture: pnl !== null && den ? pnl / den : null,
    atTakeProfit: value !== null && tp !== null && value >= tp,
    atStop: value !== null && stop !== null && value <= stop,
    legsQuoted: quoted.length,
    legsTotal: legs.length,
  };
}

function liveAccount(a: Rec, fresh: Fresh): ManageLiveAccount {
  const p = rec(a["portfolio"]);
  const bench = typeof p["benchmark"] === "string" ? p["benchmark"] : null;
  const benchSpot = bench ? (fresh.mids.get(bench) ?? null) : null;
  const betas = new Map<string, number | null>();
  for (const u of arr(p["underlyings"])) {
    const r = rec(u);
    if (typeof r["underlying"] === "string") betas.set(r["underlying"], num(r["beta"]));
  }
  let bwd: number | null = 0;
  let theta: number | null = 0;
  let total = 0;
  let withGreeks = 0;
  let marked = 0;
  let dayPl: number | null = 0;
  let moved: number | null = 0;
  for (const tRaw of arr(a["trades"])) {
    const t = rec(tRaw);
    const under = typeof t["underlying"] === "string" ? t["underlying"] : "";
    const spot = fresh.mids.get(under) ?? null;
    const beta = betas.get(under) ?? null;
    for (const raw of arr(t["legs"])) {
      const l = legOf(raw);
      if (l === null) continue;
      total += 1;
      // The day's P&L and the move since the evaluation, each null once any leg lacks a fresh mark.
      const mark = fresh.mids.get(l.key);
      const prev = num(rec(raw)["prev_close"]);
      const was = num(rec(raw)["value"]);
      if (mark !== undefined) marked += 1;
      dayPl = dayPl !== null && mark !== undefined && prev !== null ? dayPl + (mark - prev) * l.quantity * l.scale : null;
      moved = moved !== null && mark !== undefined && was !== null ? moved + mark * l.quantity * l.scale - was : null;
      const g = l.option ? fresh.greeks.get(l.key) : { delta: 1, theta: 0 };
      const ok = g && g.delta !== null && g.theta !== null;
      if (ok) withGreeks += 1;
      theta = theta !== null && ok ? theta + g.theta! * l.quantity * l.scale : null;
      const shares = ok ? g.delta! * l.quantity * l.scale : null;
      bwd =
        bwd !== null && shares !== null && beta !== null && spot !== null && benchSpot
          ? bwd + (shares * beta * spot) / benchSpot
          : null;
    }
  }
  const netLiq = num(a["net_liq"]);
  const leftOut = arr(a["excluded"]).reduce<number>((s, e) => s + (num(rec(e)["legs"]) ?? 0), 0);
  return {
    account: accountName(a),
    bwd: total > 0 ? bwd : null,
    theta: total > 0 ? theta : null,
    legsWithGreeks: withGreeks,
    legsTotal: total,
    legsMarked: marked,
    dayPl: total > 0 ? dayPl : null,
    netLiqEstimate: total > 0 && moved !== null && netLiq !== null ? netLiq + moved : null,
    legsLeftOut: leftOut,
  };
}

export function readManageLive(
  config: ConsoleConfig,
  now: number = Date.now(),
  closed: boolean = sessionClosed(config, now),
): ManageLivePayload {
  const sessions = listSessions(config);
  const session = sessions[sessions.length - 1] ?? null;
  const maxAge = closed ? CLOSED_MAX_AGE_S : MAX_AGE_S;
  const empty: ManageLivePayload = { session, oldestQuoteAt: null, maxAgeSeconds: maxAge, closedMarks: closed, trades: [], accounts: [], spots: {}, marks: {} };
  if (session === null) return empty;
  let doc: Rec;
  try {
    doc = rec(JSON.parse(fs.readFileSync(path.join(config.paths.manageDir, `eval-${session}.json`), "utf-8")));
  } catch {
    return empty;
  }
  const accounts = arr(doc["accounts"]).map(rec);
  // An account on a broker login with a producer of its own (`stream_instances`, written by the
  // orchestrator's snapshot) is quoted from that producer's cache, as manage declared its legs there;
  // every other account, and the benchmark, from the shared one. Never one login's quotes for
  // another's legs: an account whose own producer is down shows unmarked.
  const instances = new Set(arr(doc["stream_instances"]).filter((s): s is string => typeof s === "string"));
  const sourceOf = (a: Rec): string => {
    const login = typeof a["login"] === "string" ? a["login"] : "main";
    return instances.has(login) ? login : "main";
  };
  const wanted = new Map<string, Set<string>>([["main", new Set()]]);
  // Each option leg's contract symbol (padding collapsed) to its cache key, for `marks`.
  const contracts = new Map<string, string>();
  for (const a of accounts) {
    const symbols = wanted.get(sourceOf(a)) ?? new Set<string>();
    wanted.set(sourceOf(a), symbols);
    const bench = rec(a["portfolio"])["benchmark"];
    if (typeof bench === "string") symbols.add(bench);
    for (const t of arr(a["trades"]).map(rec)) {
      if (typeof t["underlying"] === "string") symbols.add(t["underlying"]);
      for (const l of arr(t["legs"])) {
        const leg = legOf(l);
        if (!leg) continue;
        symbols.add(leg.key);
        if (leg.option) contracts.set(contractKey(String(rec(l)["symbol"])), leg.key);
      }
    }
  }
  const freshBy = new Map<string, Fresh>();
  for (const [source, symbols] of wanted) {
    freshBy.set(source, readFresh(config, [...symbols], now, maxAge, instanceCachePath(config, source)));
  }
  const freshOf = (a: Rec): Fresh => freshBy.get(sourceOf(a)) ?? freshBy.get("main")!;
  // The page-wide spot and contract maps: main's first, then each instance's for what main lacks.
  const spots: Record<string, number> = {};
  const marks: Record<string, number> = {};
  let oldest: number | null = null;
  for (const [source, symbols] of wanted) {
    const fresh = freshBy.get(source)!;
    if (fresh.oldest !== null) oldest = oldest === null ? fresh.oldest : Math.min(oldest, fresh.oldest);
    for (const s of symbols) {
      const m = fresh.mids.get(s);
      if (!s.startsWith(".") && m !== undefined && !(s in spots)) spots[s] = m;
    }
    for (const [contract, key] of contracts) {
      const m = fresh.mids.get(key);
      if (m !== undefined && !(contract in marks)) marks[contract] = m;
    }
  }
  return {
    session,
    oldestQuoteAt: oldest,
    maxAgeSeconds: maxAge,
    closedMarks: closed,
    trades: accounts.flatMap((a) => arr(a["trades"]).map((t) => liveTrade(rec(t), freshOf(a)))),
    accounts: accounts.map((a) => liveAccount(a, freshOf(a))),
    spots,
    marks,
  };
}

/** A contract symbol with its padding collapsed, as manage's wheel and the page key it. */
export function contractKey(symbol: string): string {
  return symbol.split(/\s+/).filter(Boolean).join(" ");
}
