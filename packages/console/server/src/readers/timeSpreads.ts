/**
 * The time-spread scanner's file for the Screeners page's time-spreads tab, and the account's rows for
 * Live, portfolio. `scripts/fetch_time_spreads.py` writes one file a
 * session (`time-spreads/<session>.json`): every name's term-structure row and the shortlist's
 * structures as manage builds them; manage sizes them (`book.time_spreads`). This renames and decides
 * nothing. NO TESTED EDGE: rows come in the producers' order, by the untested front/back ratio.
 */
import fs from "node:fs";
import path from "node:path";
import type {
  ManageTimeSpreads,
  TimeSpreadLeg,
  TimeSpreadStructure,
  TimeSpreadTerm,
  TimeSpreadsPayload,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str } from "./db.js";

const DAY = /^\d{4}-\d{2}-\d{2}$/;

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function counts(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, n] of Object.entries(rec(v))) if (typeof n === "number") out[k] = n;
  return out;
}

function band(v: unknown): [number | null, number | null] | null {
  return Array.isArray(v) && v.length === 2 ? [num(v[0]), num(v[1])] : null;
}

function expiry(v: unknown): TimeSpreadTerm["front"] {
  const e = rec(v);
  const exp = str(e["expiration"]);
  const dte = num(e["dte"]);
  const iv = num(e["iv"]);
  return exp && dte !== null && iv !== null ? { expiration: exp, dte, iv } : null;
}

/** The scanner's data directory: beside manage's. */
export function timeSpreadsDir(config: ConsoleConfig): string {
  return path.join(path.dirname(config.paths.manageDir), "time-spreads");
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

function legOf(v: unknown): TimeSpreadLeg | null {
  const l = rec(v);
  if (Object.keys(l).length === 0) return null;
  const right = str(l["right"]);
  return { strike: num(l["strike"]), right: right === "C" || right === "P" ? right : null, option: str(l["option"]) };
}

function quotesOf(v: unknown): TimeSpreadStructure["entryQuotes"] {
  const q = arr(v);
  if (q.length !== 2) return null;
  const pair = (x: unknown): [number | null, number | null] => {
    const a = arr(x);
    return [num(a[0]), num(a[1])];
  };
  return [pair(q[0]), pair(q[1])];
}

export function structureOf(v: unknown): TimeSpreadStructure {
  const r = rec(v);
  const dir = str(r["direction"]);
  return {
    underlying: str(r["underlying"]) ?? "",
    kind: str(r["kind"]) ?? "",
    direction: dir === "bullish" || dir === "bearish" ? dir : "neutral",
    label: str(r["label"]) ?? "",
    front: str(r["front"]) ?? "",
    back: str(r["back"]) ?? "",
    spot: num(r["spot"]),
    ratio: num(r["ratio"]),
    ivRank: num(r["iv_rank"]),
    sector: str(r["sector"]),
    earningsDate: str(r["earnings_date"]),
    longLeg: legOf(r["long"]),
    shortLeg: legOf(r["short"]),
    entryQuotes: quotesOf(r["entry_quotes"]),
    debitMid: num(r["debit_mid"]),
    debitNatural: num(r["debit_natural"]),
    gap: num(r["gap"]),
    debitShareOfGap: num(r["debit_share_of_gap"]),
    frontIv: num(r["front_iv"]),
    backIv: num(r["back_iv"]),
    netDelta: num(r["net_delta"]),
    thetaDay: num(r["theta_day"]),
    pnlFlat: num(r["pnl_flat"]),
    pnlUp5: num(r["pnl_up_5"]),
    pnlDown10: num(r["pnl_down_10"]),
    losesUnchanged: r["loses_unchanged"] === true,
    breakeven: band(r["breakeven"]),
    priceStop: band(r["price_stop"]),
    takeProfit: num(r["take_profit"]),
    valueStop: num(r["value_stop"]),
    rollBy: str(r["roll_by"]),
    maxLoss: num(r["max_loss"]),
    units: num(r["units"]),
    binding: str(r["binding"]),
    heldName: r["held_name"] === true,
    heldSector: r["held_sector"] === true,
    bpAfterPct: num(r["bp_after_pct"]),
  };
}

function termOf(v: unknown): TimeSpreadTerm {
  const r = rec(v);
  return {
    symbol: str(r["symbol"]) ?? "",
    ratio: num(r["ratio"]),
    ivRank: num(r["iv_rank"]),
    front: expiry(r["front"]),
    back: expiry(r["back"]),
    earningsDate: str(r["earnings_date"]),
    sector: str(r["sector"]),
    passes: r["passes"] === true,
    why: arr(r["why"]).filter((w): w is string => typeof w === "string"),
  };
}

/**
 * The account's sizing of a day's scan: the first account in that day's manage evaluation that carries
 * `portfolio.time_spreads`. Never returns an account number. The note says why there is nothing.
 */
export function readAccountTimeSpreads(
  config: ConsoleConfig,
  day: string,
): { account: ManageTimeSpreads | null; accountNote: string | null } {
  let doc: Record<string, unknown>;
  try {
    doc = rec(JSON.parse(fs.readFileSync(path.join(config.paths.manageDir, `eval-${day}.json`), "utf-8")));
  } catch {
    return { account: null, accountNote: `no manage evaluation for ${day}, so nothing is sized to the account` };
  }
  for (const a of arr(doc["accounts"])) {
    const ts = rec(rec(a)["portfolio"])["time_spreads"];
    if (ts !== undefined && ts !== null) return { account: manageTimeSpreads(ts), accountNote: null };
  }
  return { account: null, accountNote: `the ${day} evaluation carries no time-spread rows, so nothing is sized to the account` };
}

const EMPTY: Omit<TimeSpreadsPayload, "day" | "days" | "error"> = {
  account: null,
  accountNote: null,
  takenAt: null,
  complete: false,
  problem: null,
  order: null,
  universe: null,
  read: null,
  passing: null,
  shortlist: [],
  terms: [],
  structures: [],
  dropped: {},
};

export function readTimeSpreads(config: ConsoleConfig, day: string | null): TimeSpreadsPayload {
  const dir = timeSpreadsDir(config);
  const listed = days(dir);
  const want = day && DAY.test(day) ? day : (listed[0] ?? null);
  if (!want) {
    return { ...EMPTY, day: null, days: listed, error: "no time-spread scan yet (scripts/fetch_time_spreads.py, about 10:30 ET)" };
  }
  let doc: Record<string, unknown>;
  try {
    doc = rec(JSON.parse(fs.readFileSync(path.join(dir, `${want}.json`), "utf-8")));
  } catch {
    return { ...EMPTY, day: want, days: listed, error: `no time-spread scan for ${want}` };
  }
  const terms = arr(doc["rows"]).map(termOf);
  terms.sort((a, b) =>
    a.passes !== b.passes ? (a.passes ? -1 : 1) : a.passes ? (b.ratio ?? 0) - (a.ratio ?? 0) : a.symbol.localeCompare(b.symbol),
  );
  return {
    day: want,
    days: listed,
    takenAt: str(doc["taken_at"]),
    complete: doc["complete"] === true,
    problem: str(doc["problem"]),
    order: str(doc["order"]),
    universe: num(doc["universe"]),
    read: num(doc["read"]),
    passing: num(doc["passing"]),
    shortlist: arr(doc["shortlist"]).filter((s): s is string => typeof s === "string"),
    terms,
    structures: arr(doc["structures"]).map(structureOf),
    dropped: counts(doc["structures_dropped"]),
    ...readAccountTimeSpreads(config, want),
    error: null,
  };
}

/** `book.time_spreads` as Live, portfolio reads it; null on an evaluation from before it. */
export function manageTimeSpreads(v: unknown): ManageTimeSpreads | null {
  if (v === undefined || v === null) return null;
  const r = rec(v);
  const df = rec(r["delta_filter"]);
  return {
    sourceSession: str(r["source_session"]),
    takenAt: str(r["taken_at"]),
    order: str(r["order"]),
    edge: str(r["edge"]),
    deltaFilter:
      Object.keys(df).length === 0
        ? null
        : { bookBwd: num(df["book_bwd"]), limit: num(df["limit"]), bullishHeldBack: df["bullish_held_back"] === true },
    bpCapPct: num(r["bp_cap_pct"]),
    fits: arr(r["fits"]).map(structureOf),
    fitsTotal: num(r["fits_total"]) ?? 0,
    tooBig: arr(r["too_big"]).map(structureOf),
    tooBigTotal: num(r["too_big_total"]) ?? 0,
    dropped: counts(r["dropped"]),
    error: str(r["error"]),
  };
}
