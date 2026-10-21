/**
 * The skate screen for the Screeners page's Skate tab (2026-10-19; docs/skate-plan.md step 3).
 *
 * `fetch_screener_greeks.py skate` quotes each name-side's range-band strikes in regular hours (12:00
 * and 15:05 ET) and scores them with `cherrypick.technicals.skate` (`score`, `best_expiries`), writing
 * `market-report/screener-greeks/skate/<session>/<HHMM>.json`. A `*.trial.json` beside them is a hand
 * run and is never read.
 *
 * This reads the newest run of a session and renames its fields; it decides nothing. Per name-side it
 * carries each band's best expiry, as the file states it, and the band's pick leg at that expiry: the
 * lookup is by the best's own expiry and strike, never a re-ranking. The other legs (the neighbours,
 * the other expiries) stay in the file, which is ~4.5 MB a run.
 *
 * A missing run, an unreadable file, an incomplete run and the run's own `problem` are all stated in
 * the payload, so the page never shows an empty table that reads as "no trades".
 */

import fs from "node:fs";
import path from "node:path";
import type {
  SkateBand,
  SkateBandRead,
  SkateBest,
  SkateConfig,
  SkateLeg,
  SkatePayload,
  SkateRow,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str } from "./db.js";
import { marketReportDir } from "./screeners.js";

const DAY = /^\d{4}-\d{2}-\d{2}$/;
/** A scheduled run: `1200.json`, `1505.json`. A trial run (`1852.trial.json`) does not match. */
const RUN = /^(\d{4})\.json$/;

/** The file's band names against ours. */
const FILE_BAND: Record<SkateBand, string> = { fixed: "fixed", byExpiry: "by_expiry" };

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

export function skateDir(config: ConsoleConfig): string {
  return path.join(marketReportDir(config), "screener-greeks", "skate");
}

/** A session's scheduled runs, newest first. */
function runsOf(config: ConsoleConfig, day: string): string[] {
  try {
    return fs
      .readdirSync(path.join(skateDir(config), day))
      .map((n) => RUN.exec(n)?.[1] ?? null)
      .filter((n): n is string => n !== null)
      .sort()
      .reverse();
  } catch {
    return [];
  }
}

/** Sessions holding at least one scheduled run, newest first (`chains/` and trial-only days are not sessions). */
export function skateDays(config: ConsoleConfig): string[] {
  try {
    return fs
      .readdirSync(skateDir(config), { withFileTypes: true })
      .filter((e) => e.isDirectory() && DAY.test(e.name) && runsOf(config, e.name).length > 0)
      .map((e) => e.name)
      .sort()
      .reverse();
  } catch {
    return [];
  }
}

function leg(v: unknown): SkateLeg | null {
  const l = rec(v);
  const expiry = str(l["expiry"]);
  const strike = num(l["strike"]);
  if (expiry === null || strike === null) return null;
  const q = rec(l["quote"]);
  const s = rec(l["soft"]);
  return {
    expiry,
    days: num(l["days"]) ?? 0,
    role: str(l["role"]) ?? "",
    option: str(l["option"]) ?? "",
    sameBand: l["same_band"] === true,
    strike,
    offset: num(l["offset"]),
    outsideGrid: l["outside_grid"] === true,
    quote: { bid: num(q["bid"]), ask: num(q["ask"]), volume: num(q["volume"]), openInterest: num(q["open_interest"]) },
    mid: num(l["mid"]),
    iv: num(l["iv"]),
    delta: num(l["delta"]),
    impliedSkate: num(l["implied_skate"]),
    skate: num(l["skate"]),
    skateRecent: num(l["skate_recent"]),
    gap: num(l["gap"]),
    yieldBid: num(l["yield_bid"]),
    yieldMid: num(l["yield_mid"]),
    perDayBid: num(l["per_day_bid"]),
    historyWindows: num(l["history_windows"]),
    historyWindowsRecent: num(l["history_windows_recent"]),
    feeOpen: num(l["fee_open"]),
    netPremiumBid: num(l["net_premium_bid"]),
    netYieldBid: num(l["net_yield_bid"]),
    testedPerCycle: num(l["tested_per_cycle"]),
    testedPerMonth: num(l["tested_per_month"]),
    // Only an explicit true passes: a leg with no verdict is not shown as passing.
    hard: l["hard"] === true,
    soft: { skate: s["skate"] === true, skateRecent: s["skate_recent"] === true, yield: s["yield"] === true, all: s["all"] === true },
  };
}

function best(v: unknown): SkateBest | null {
  const b = rec(v);
  const expiry = str(b["expiry"]);
  const strike = num(b["strike"]);
  if (expiry === null || strike === null) return null;
  return {
    expiry,
    strike,
    perDayBid: num(b["per_day_bid"]),
    skate: num(b["skate"]),
    monthly: str(b["monthly"]),
    vsMonthly: num(b["vs_monthly"]),
  };
}

function bandRead(r: Record<string, unknown>, band: SkateBand): SkateBandRead {
  const b = best(rec(r["best"])[FILE_BAND[band]]);
  if (b === null) return { best: null, pick: null };
  const raw = (Array.isArray(r["legs"]) ? r["legs"] : []).find((v) => {
    const l = rec(v);
    return l["band"] === FILE_BAND[band] && l["role"] === "pick" && l["expiry"] === b.expiry && l["strike"] === b.strike;
  });
  return { best: b, pick: raw === undefined ? null : leg(raw) };
}

function row(v: unknown): SkateRow | null {
  const r = rec(v);
  const symbol = str(r["symbol"]);
  const side = r["side"] === "C" || r["side"] === "P" ? r["side"] : null;
  if (symbol === null || side === null) return null;
  return {
    symbol,
    side,
    spot: num(r["spot"]),
    fund: r["fund"] === true,
    earnings: str(r["earnings"]),
    divYield: num(r["div_yield"]),
    stage: num(r["stage"]),
    monthly: str(r["monthly"]),
    bands: { fixed: bandRead(r, "fixed"), byExpiry: bandRead(r, "byExpiry") },
  };
}

function configOf(v: unknown): SkateConfig | null {
  const c = rec(v);
  if (Object.keys(c).length === 0) return null;
  const hard = rec(c["hard"]);
  const soft = rec(c["soft"]);
  const steps = Array.isArray(c["by_expiry"]) ? c["by_expiry"] : [];
  return {
    haircut: num(c["haircut"]),
    byExpiry: steps
      .map((s) => (Array.isArray(s) && num(s[0]) !== null && num(s[1]) !== null ? ([s[0], s[1]] as [number, number]) : null))
      .filter((s): s is [number, number] => s !== null),
    hard: { minOpenInterest: num(hard["min_open_interest"]), maxSpreadPct: num(hard["max_spread_pct"]), maxSpreadAbs: num(hard["max_spread_abs"]) },
    soft: { skate: num(soft["skate"]), skateRecent: num(soft["skate_recent"]), yield: num(soft["yield"]) },
    stageTwo: num(c["stage_two"]),
    monthDays: num(c["month_days"]),
  };
}

/** The last parse, keyed on the file's stamp: the page polls, and a run is ~4.5 MB. */
let memo: { key: string; payload: SkatePayload } | null = null;

export function readSkate(config: ConsoleConfig, wanted?: string | null): SkatePayload {
  const days = skateDays(config);
  const day = wanted && days.includes(wanted) ? wanted : (days[0] ?? null);
  const runs = day === null ? [] : runsOf(config, day);
  const run = runs[0] ?? null;
  const empty: SkatePayload = {
    status: "none",
    day,
    days,
    run,
    runs,
    measuredAt: null,
    bandDay: null,
    complete: false,
    problem: null,
    config: null,
    rows: [],
  };
  if (day === null || run === null) return empty;
  const file = path.join(skateDir(config), day, `${run}.json`);
  let key: string;
  try {
    const st = fs.statSync(file);
    key = `${file}|${st.mtimeMs}|${st.size}|${days.join(",")}`;
  } catch {
    return empty;
  }
  if (memo !== null && memo.key === key) return memo.payload;
  let doc: Record<string, unknown>;
  try {
    doc = rec(JSON.parse(fs.readFileSync(file, "utf-8")));
  } catch {
    return { ...empty, status: "unreadable" };
  }
  const payload: SkatePayload = {
    status: "ok",
    day,
    days,
    run,
    runs,
    measuredAt: str(doc["measured_at"]),
    bandDay: str(doc["band_day"]),
    complete: doc["complete"] === true,
    problem: str(doc["problem"]),
    config: configOf(doc["config"]),
    rows: Array.isArray(doc["rows"]) ? doc["rows"].map(row).filter((r): r is SkateRow => r !== null) : [],
  };
  memo = { key, payload };
  return payload;
}
