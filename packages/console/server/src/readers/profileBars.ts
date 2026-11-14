/**
 * The Market Profile bars `scripts/fetch_profile_bars.py` records (`data/market-files/profile_bars.db`),
 * for the Charts → profile tab. Read-only.
 *
 * A query, not a derivation: the newest N regular-session days of one symbol, the minutes folded into
 * 5-minute bars (first open, highest high, lowest low, last close, volume summed). Five minutes divides
 * the profile's 30-minute period, so each period's rows are the same as from the minutes. The profile
 * and composite are built in the browser by `lib/marketProfile`, the mirror of
 * `cherrypick.core.marketprofile`; nothing here decides a POC or a value area.
 *
 * A missing store or a symbol with nothing recorded is `available: false` with the reason, never an
 * empty chart that reads as a quiet market.
 */
import path from "node:path";
import { PROFILE_SYMBOLS, type CandleBar, type ProfileBarsPayload, type ProfileHours, type ProfileSymbol } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readOnlyDb } from "./db.js";

export const PROFILE_BAR_SECONDS = 300;
export const DEFAULT_SESSIONS = 10;
export const MAX_SESSIONS = 40;

export function isProfileSymbol(s: unknown): s is ProfileSymbol {
  return typeof s === "string" && (PROFILE_SYMBOLS as readonly string[]).includes(s);
}

/** A `?sessions=` value as a count in [1, MAX_SESSIONS]; anything unreadable is the default. */
export function sessionCount(raw: unknown): number {
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1) return DEFAULT_SESSIONS;
  return Math.min(n, MAX_SESSIONS);
}

export function profileBarsPath(config: ConsoleConfig): string {
  return path.join(config.paths.cherrypick, "data", "market-files", "profile_bars.db");
}

/** Minute rows -> 5-minute bars, oldest first. `ts_ms` is the minute's start. */
export function foldBars(rows: ReadonlyArray<{ ts_ms: number; o: number; h: number; l: number; c: number; v: number }>): CandleBar[] {
  const width = PROFILE_BAR_SECONDS * 1000;
  const out = new Map<number, CandleBar>();
  for (const r of [...rows].sort((a, b) => a.ts_ms - b.ts_ms)) {
    const start = Math.floor(r.ts_ms / width) * width;
    const held = out.get(start);
    if (held === undefined) out.set(start, { t: start / 1000, o: r.o, h: r.h, l: r.l, c: r.c, v: r.v });
    else {
      held.h = Math.max(held.h, r.h);
      held.l = Math.min(held.l, r.l);
      held.c = r.c;
      held.v += r.v;
    }
  }
  return [...out.values()];
}

function absent(symbol: ProfileSymbol, reason: string, hours: ProfileHours): ProfileBarsPayload {
  return {
    symbol,
    available: false,
    reason,
    hours,
    sessions: [],
    barSeconds: PROFILE_BAR_SECONDS,
    bars: [],
    contracts: {},
    newestSession: null,
    fetchedAt: null,
  };
}

export function readProfileBars(
  config: ConsoleConfig,
  symbol: ProfileSymbol,
  sessions: number,
  hours: ProfileHours = "regular",
): ProfileBarsPayload {
  const dbPath = profileBarsPath(config);
  // Extended: every bar from the oldest session's day on, overnight and after the newest session
  // included, for the untested-POC rule (a future's overnight can trade through a POC). The sessions
  // are still the regular sessions held; a profile reads only their regular hours either way.
  const hoursClause = hours === "extended" ? "" : "AND et_time >= '09:30' AND et_time < '16:00'";
  const outcome = readOnlyDb(dbPath, (db) => {
    const days = db
      .prepare<[string, number], { et_day: string }>(
        "SELECT DISTINCT et_day FROM profile_bars WHERE symbol = ? AND et_time >= '09:30' AND et_time < '16:00' " +
          "ORDER BY et_day DESC LIMIT ?",
      )
      .all(symbol, sessions)
      .map((r) => r.et_day)
      .reverse();
    if (days.length === 0) return null;
    const marks = days.map(() => "?").join(",");
    const dayClause = hours === "extended" ? "et_day >= ?" : `et_day IN (${marks})`;
    const rows = db
      .prepare(
        `SELECT ts_ms, open AS o, high AS h, low AS l, close AS c, volume AS v, et_day, et_time, contract FROM profile_bars ` +
          `WHERE symbol = ? AND ${dayClause} ${hoursClause} ORDER BY ts_ms`,
      )
      .all(symbol, ...(hours === "extended" ? [days[0]!] : days)) as Array<{
      ts_ms: number;
      o: number;
      h: number;
      l: number;
      c: number;
      v: number;
      et_day: string;
      et_time: string;
      contract: string;
    }>;
    const contracts: Record<string, string> = {};
    for (const r of rows) if (r.et_time >= "09:30" && r.et_time < "16:00") contracts[r.et_day] ??= r.contract;
    const fetched = db
      .prepare<[string], { f: number | null }>("SELECT MAX(fetched_at) AS f FROM profile_bars WHERE symbol = ?")
      .get(symbol);
    return { days, rows, contracts, fetchedAt: fetched?.f ?? null };
  });
  if (outcome.status === "absent") return absent(symbol, "no profile bars recorded yet: the profile-bars job (17:15 ET) has not run", hours);
  if (outcome.status === "failed") return absent(symbol, `the profile bar store could not be read: ${outcome.error}`, hours);
  if (outcome.value === null) return absent(symbol, `no regular-session bars recorded for ${symbol}`, hours);
  const { days, rows, contracts, fetchedAt } = outcome.value;
  return {
    symbol,
    available: true,
    reason: null,
    hours,
    sessions: days,
    barSeconds: PROFILE_BAR_SECONDS,
    bars: foldBars(rows),
    contracts,
    newestSession: days.at(-1) ?? null,
    fetchedAt: fetchedAt === null ? null : new Date(fetchedAt * 1000).toISOString(),
  };
}
