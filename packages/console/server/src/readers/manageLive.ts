import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";
import type { ManageLiveAccount, ManageLivePayload, ManageLiveTrade } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
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

interface Fresh {
  mids: Map<string, number>;
  greeks: Map<string, { delta: number | null; theta: number | null }>;
  oldest: number | null;
}

function readFresh(config: ConsoleConfig, symbols: string[], now: number): Fresh {
  const out: Fresh = { mids: new Map(), greeks: new Map(), oldest: null };
  const p = config.paths.streamCacheDb;
  if (symbols.length === 0 || !fs.existsSync(p)) return out;
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    const cutoff = now / 1000 - MAX_AGE_S;
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
  for (const tRaw of arr(a["trades"])) {
    const t = rec(tRaw);
    const under = typeof t["underlying"] === "string" ? t["underlying"] : "";
    const spot = fresh.mids.get(under) ?? null;
    const beta = betas.get(under) ?? null;
    for (const raw of arr(t["legs"])) {
      const l = legOf(raw);
      if (l === null) continue;
      total += 1;
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
  return {
    account: typeof a["account"] === "string" ? a["account"] : null,
    bwd: total > 0 ? bwd : null,
    theta: total > 0 ? theta : null,
    legsWithGreeks: withGreeks,
    legsTotal: total,
  };
}

export function readManageLive(config: ConsoleConfig, now: number = Date.now()): ManageLivePayload {
  const sessions = listSessions(config);
  const session = sessions[sessions.length - 1] ?? null;
  const empty: ManageLivePayload = { session, oldestQuoteAt: null, maxAgeSeconds: MAX_AGE_S, trades: [], accounts: [], spots: {} };
  if (session === null) return empty;
  let doc: Rec;
  try {
    doc = rec(JSON.parse(fs.readFileSync(path.join(config.paths.manageDir, `eval-${session}.json`), "utf-8")));
  } catch {
    return empty;
  }
  const accounts = arr(doc["accounts"]).map(rec);
  const symbols = new Set<string>();
  for (const a of accounts) {
    const bench = rec(a["portfolio"])["benchmark"];
    if (typeof bench === "string") symbols.add(bench);
    for (const t of arr(a["trades"]).map(rec)) {
      if (typeof t["underlying"] === "string") symbols.add(t["underlying"]);
      for (const l of arr(t["legs"])) {
        const leg = legOf(l);
        if (leg) symbols.add(leg.key);
      }
    }
  }
  const fresh = readFresh(config, [...symbols], now);
  const spots: Record<string, number> = {};
  for (const s of symbols) {
    const m = fresh.mids.get(s);
    if (!s.startsWith(".") && m !== undefined) spots[s] = m;
  }
  return {
    session,
    oldestQuoteAt: fresh.oldest,
    maxAgeSeconds: MAX_AGE_S,
    trades: accounts.flatMap((a) => arr(a["trades"]).map((t) => liveTrade(rec(t), fresh))),
    accounts: accounts.map((a) => liveAccount(a, fresh)),
    spots,
  };
}
