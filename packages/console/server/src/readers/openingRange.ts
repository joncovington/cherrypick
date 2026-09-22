/**
 * The opening range (09:30-10:00 ET) from the gex recorder's spot trail.
 *
 * Reading another package's store read-only is the ordinary relationship here — `fliesLive.ts`
 * already reads `gex_spot_history` the same way. What this reader deliberately does NOT do is
 * classify: no ATR, no efficiency ratio, no regime. Those definitions live once, in
 * `cherrypick.core.openingrange`, and a TypeScript copy would drift at the edges that matter.
 * Bucketing and min/max are the whole computation, and each has one meaning.
 *
 * A window missing any of its six buckets returns `complete: false` with every measure null. A
 * range over four buckets is not a range, and rendering it as one — or as zero — would be a
 * quietly wrong number on screen at the exact moment entries begin.
 */
import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import type { OpeningRangeBucket, OpeningRangePayload } from "@console/shared";
import { withReadOnlyDb } from "./db.js";

const ET = "America/New_York";
const OPEN_MIN = 9 * 60 + 30;
const ENTRY_MIN = 10 * 60;
const BUCKET_MINUTES = 5;
const EXPECTED_BUCKETS = (ENTRY_MIN - OPEN_MIN) / BUCKET_MINUTES;

/** Minute-of-day in ET. Wall clock via Intl, matching `fliesLive.ts` so DST cannot desync them. */
function etMinuteOfEpoch(ts: number): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: ET,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date(ts * 1000));
  const h = Number(parts.find((p) => p.type === "hour")?.value ?? "0") % 24;
  const m = Number(parts.find((p) => p.type === "minute")?.value ?? "0");
  return h * 60 + m;
}

function absent(session: string | null, symbol: string, reason: string): OpeningRangePayload {
  return {
    session,
    symbol,
    complete: false,
    reason,
    bucketsPresent: 0,
    bucketsExpected: EXPECTED_BUCKETS,
    high: null,
    low: null,
    rangePoints: null,
    first: null,
    last: null,
    buckets: [],
  };
}

export function readOpeningRange(
  config: ConsoleConfig,
  session: string | null,
  symbol = "SPX",
): OpeningRangePayload {
  if (session === null) return absent(null, symbol, "no session selected");
  const dbPath = path.join(config.paths.gexDir, "gex_history.db");
  const ticks = withReadOnlyDb(dbPath, [] as Array<{ ts: number; spot: number }>, (db) =>
    db
      .prepare<[string, string], Record<string, unknown>>(
        "SELECT ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date = ? ORDER BY ts",
      )
      .all(symbol, session)
      .map((r) => ({ ts: Number(r["ts"]), spot: Number(r["spot"]) }))
      .filter((r) => Number.isFinite(r.ts) && Number.isFinite(r.spot) && r.spot > 0),
  );
  if (ticks.length === 0) return absent(session, symbol, "no spot trail recorded for this session");

  const byBucket = new Map<number, number[]>();
  for (const tick of ticks) {
    const minute = etMinuteOfEpoch(tick.ts);
    if (minute < OPEN_MIN || minute >= ENTRY_MIN) continue;
    const index = Math.floor((minute - OPEN_MIN) / BUCKET_MINUTES);
    const held = byBucket.get(index);
    if (held) held.push(tick.spot);
    else byBucket.set(index, [tick.spot]);
  }

  const buckets: OpeningRangeBucket[] = [...byBucket.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([index, prices]) => ({
      minute: OPEN_MIN + index * BUCKET_MINUTES,
      open: prices[0]!,
      high: Math.max(...prices),
      low: Math.min(...prices),
      close: prices[prices.length - 1]!,
      ticks: prices.length,
    }));

  if (buckets.length < EXPECTED_BUCKETS) {
    return {
      ...absent(session, symbol, `window has ${buckets.length} of ${EXPECTED_BUCKETS} buckets`),
      bucketsPresent: buckets.length,
      buckets,
    };
  }

  const high = Math.max(...buckets.map((b) => b.high));
  const low = Math.min(...buckets.map((b) => b.low));
  return {
    session,
    symbol,
    complete: true,
    reason: null,
    bucketsPresent: buckets.length,
    bucketsExpected: EXPECTED_BUCKETS,
    high,
    low,
    rangePoints: high - low,
    first: buckets[0]!.open,
    last: buckets[buckets.length - 1]!.close,
    buckets,
  };
}
