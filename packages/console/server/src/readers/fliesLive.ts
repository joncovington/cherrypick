/**
 * The Live page's one payload: the flies live pilot's day, composed from readers that already
 * exist plus three reads that did not (2026-09-17).
 *
 * Composition, in the spirit of `readers/desk.ts`: the arming strip is `services/liveLock`, the
 * loop pill is `readFliesLoopStatus`, the activity feed is `readFliesJournal`, the at-risk figure
 * is `readFliesAnalytics`. New here: the period tiles (settled net, the core.ledgers flies rule
 * mirrored -- `gross_pnl - fees` over `status = 'settled'` rows -- and pinned by a test), the
 * intraday series (SPX from the gex recorder's spot trail, VIX/VIX3M from its regime history,
 * mark P&L and buying power from the live loop's own `fly_live_marks`), and the broker balance
 * through `services/brokerBridge`.
 *
 * `readOnlyDb`, not `withReadOnlyDb`, for the live ledger: on this page "no live ledger yet" and
 * "the read threw" must be distinguishable, and the payload says which.
 */
import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../config.js";
import type { LiveFeedRow, LiveFliesPayload, LiveGap, LivePeriod, LivePoint, LivePosition } from "@console/shared";
import { readOnlyDb, withReadOnlyDb, num, str, readJson } from "./db.js";
import { readFliesAnalytics, readFliesJournal, readFliesLoopStatus } from "./flies.js";
import { readLockStatus, sessionDateEt } from "../services/liveLock.js";
import { readBrokerAccount } from "../services/brokerBridge.js";

const ET = "America/New_York";
const RTH_OPEN_MIN = 9 * 60 + 30;
const RTH_CLOSE_MIN = 16 * 60;

// --------------------------------------------------------------------------- period bounds (ET)
function etParts(d: Date): { y: number; m: number; day: number; dow: number } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: ET,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const dow = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(get("weekday"));
  return { y: Number(get("year")), m: Number(get("month")), day: Number(get("day")), dow };
}

function iso(y: number, m: number, d: number): string {
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** `{today, week, month, year}` as inclusive `[start, end]` ISO session-date bounds, ET. The
 *  week starts Monday; a Saturday/Sunday read still points at the week just traded. */
export function periodBounds(session: string, now: Date = new Date()): Record<"today" | "week" | "month" | "year", [string, string]> {
  const { y, m, day, dow } = etParts(now);
  const back = dow === 0 ? 6 : dow - 1; // days since Monday
  const monday = new Date(Date.UTC(y, m - 1, day - back));
  const mondayIso = iso(monday.getUTCFullYear(), monday.getUTCMonth() + 1, monday.getUTCDate());
  return {
    today: [session, session],
    week: [mondayIso, session],
    month: [iso(y, m, 1), session],
    year: [iso(y, 1, 1), session],
  };
}

// --------------------------------------------------------------------------- settled net
/** core.ledgers `_flies_closed`: closed = `status = 'settled'`, net = `(gross_pnl or 0) - (fees or 0)`,
 *  session = `trade_date`. Mirrored here rather than bridged because it is a query, not a
 *  derivation; `server/test/flies-live-reader.test.ts` pins it against fixture rows. */
function settledNet(db: Database.Database, bounds: [string, string], arm: string | null): Omit<LivePeriod, "paperNet"> {
  const armClause = arm !== null ? " AND arm = ?" : "";
  const params: Array<string> = [bounds[0], bounds[1], ...(arm !== null ? [arm] : [])];
  const row = db
    .prepare<string[], Record<string, unknown>>(
      `SELECT COALESCE(SUM(COALESCE(gross_pnl, 0) - COALESCE(fees, 0)), 0) AS net,
              COUNT(*) AS trades, COUNT(DISTINCT trade_date) AS sessions
         FROM fly_positions
        WHERE status = 'settled' AND trade_date >= ? AND trade_date <= ?${armClause}`,
    )
    .get(...params);
  return {
    net: Number(row?.["net"] ?? 0),
    trades: Number(row?.["trades"] ?? 0),
    sessions: Number(row?.["sessions"] ?? 0),
  };
}

// --------------------------------------------------------------------------- the day's rows
function positionsFor(db: Database.Database, session: string): LivePosition[] {
  const hasMarks = db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fly_live_marks'").get() !== undefined;
  const markFor = hasMarks
    ? db.prepare<[string], Record<string, unknown>>(
        `SELECT iteration_ts, structure_mid, mark_pnl, resting_limit FROM fly_live_marks
          WHERE position_id = ? ORDER BY id DESC LIMIT 1`,
      )
    : null;
  return db
    .prepare<[string], Record<string, unknown>>(
      `SELECT position_id, kind, side, center, wing_width, quantity, net, fees, floor_dollars, risk_free,
              status, entry_time, entry_fill_status, completion_fill_status, completed_at, pnl
         FROM fly_positions WHERE trade_date = ? AND status != 'voided' ORDER BY entry_time`,
    )
    .all(session)
    .map((r) => {
      const positionId = String(r["position_id"] ?? "");
      const m = markFor?.get(positionId);
      return {
        positionId,
        kind: str(r["kind"]) ?? "",
        side: str(r["side"]) ?? "",
        center: Number(r["center"]),
        wingWidth: Number(r["wing_width"]),
        quantity: Number(r["quantity"] ?? 1),
        net: Number(r["net"] ?? 0),
        fees: Number(r["fees"] ?? 0),
        floorDollars: num(r["floor_dollars"]),
        riskFree: Boolean(num(r["risk_free"])),
        status: str(r["status"]) ?? "",
        entryTime: str(r["entry_time"]),
        entryFillStatus: str(r["entry_fill_status"]),
        completionFillStatus: str(r["completion_fill_status"]),
        completedAt: str(r["completed_at"]),
        mark:
          m !== undefined && typeof m["mark_pnl"] === "number"
            ? {
                at: String(m["iteration_ts"] ?? ""),
                structureMid: Number(m["structure_mid"]),
                markPnl: Number(m["mark_pnl"]),
                restingLimit: num(m["resting_limit"]),
              }
            : null,
        pnl: num(r["pnl"]),
      };
    });
}

function minuteOf(ts: string): number {
  const hm = ts.slice(11, 16);
  return Number(hm.slice(0, 2)) * 60 + Number(hm.slice(3, 5));
}

/** The summed mark path and the open-margin path, one point per tick, plus the feed's refusals. */
function markSeries(db: Database.Database, session: string): {
  markPnl: LivePoint[];
  openMargin: LivePoint[];
  latest: { at: string; total: number } | null;
} {
  const hasMarks = db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fly_live_marks'").get() !== undefined;
  if (!hasMarks) return { markPnl: [], openMargin: [], latest: null };
  const rows = db
    .prepare<[string], Record<string, unknown>>(
      `SELECT iteration_ts, SUM(mark_pnl) AS total, MAX(open_margin) AS margin
         FROM fly_live_marks WHERE trade_date = ? GROUP BY iteration_ts ORDER BY iteration_ts`,
    )
    .all(session);
  const markPnl: LivePoint[] = [];
  const openMargin: LivePoint[] = [];
  let latest: { at: string; total: number } | null = null;
  for (const r of rows) {
    const ts = String(r["iteration_ts"] ?? "");
    if (ts.length < 16) continue;
    const m = minuteOf(ts);
    const total = Number(r["total"]);
    if (Number.isFinite(total)) {
      markPnl.push({ m, v: total });
      latest = { at: ts, total };
    }
    const margin = Number(r["margin"]);
    if (Number.isFinite(margin)) openMargin.push({ m, v: margin });
  }
  return { markPnl, openMargin, latest };
}

function gapsFor(db: Database.Database, session: string): LiveGap[] {
  return db
    .prepare<[string], Record<string, unknown>>(
      `SELECT iteration_ts, status FROM fly_snapshots WHERE trade_date = ? AND status != 'ok' ORDER BY iteration_ts`,
    )
    .all(session)
    .map((r) => ({ m: minuteOf(String(r["iteration_ts"] ?? "")), reason: String(r["status"] ?? "") }))
    .filter((g) => Number.isFinite(g.m));
}

/** Peak-to-trough of a running path, as a positive dollar figure, and the peak itself. */
export function drawdownOf(points: LivePoint[]): { maxDrawdown: number | null; peak: number | null } {
  if (points.length === 0) return { maxDrawdown: null, peak: null };
  let peak = -Infinity;
  let worst = 0;
  for (const p of points) {
    if (p.v > peak) peak = p.v;
    const dd = peak - p.v;
    if (dd > worst) worst = dd;
  }
  return { maxDrawdown: worst, peak };
}

// --------------------------------------------------------------------------- market series (read-only, other packages' stores)
function etMinuteOfEpoch(ts: number): number {
  const d = new Date(ts * 1000);
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: ET, hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(d);
  const h = Number(parts.find((p) => p.type === "hour")?.value ?? "0") % 24;
  const m = Number(parts.find((p) => p.type === "minute")?.value ?? "0");
  return h * 60 + m;
}

function spotTrail(config: ConsoleConfig, symbol: string, session: string): Array<{ ts: number; spot: number }> {
  const p = path.join(config.paths.gexDir, "gex_history.db");
  return withReadOnlyDb(p, [] as Array<{ ts: number; spot: number }>, (db) =>
    db
      .prepare<[string, string], Record<string, unknown>>(
        "SELECT ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date = ? ORDER BY ts",
      )
      .all(symbol, session)
      .map((r) => ({ ts: Number(r["ts"]), spot: Number(r["spot"]) }))
      .filter((r) => Number.isFinite(r.ts) && Number.isFinite(r.spot)),
  );
}

function regimeSeries(config: ConsoleConfig, reading: string, session: string): LivePoint[] {
  const p = path.join(config.paths.gexDir, "gex_history.db");
  return withReadOnlyDb(p, [] as LivePoint[], (db) => {
    const has = db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_regime_history'").get();
    if (has === undefined) return [];
    return db
      .prepare<[string, string], Record<string, unknown>>(
        "SELECT ts, value FROM market_regime_history WHERE trade_date = ? AND reading = ? AND usable = 1 ORDER BY ts",
      )
      .all(session, reading)
      .map((r) => ({ m: etMinuteOfEpoch(Number(r["ts"])), v: Number(r["value"]) }))
      .filter((r) => Number.isFinite(r.m) && Number.isFinite(r.v) && r.m >= RTH_OPEN_MIN && r.m <= RTH_CLOSE_MIN);
  });
}

/** The prior close from the stream cache's per-session summary, the baseline a % change is honest against. */
function prevClose(config: ConsoleConfig, symbol: string, session: string): number | null {
  const p = config.paths.streamCacheDb;
  if (!fs.existsSync(p)) return null;
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    const row = db
      .prepare<[string, string], Record<string, unknown>>(
        "SELECT prev_day_close FROM stream_summary WHERE symbol = ? AND trade_date = ?",
      )
      .get(symbol, session);
    const v = Number(row?.["prev_day_close"]);
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch {
    return null;
  } finally {
    db?.close();
  }
}

// --------------------------------------------------------------------------- the payload
export function readFliesLive(config: ConsoleConfig, session: string | null = null): LiveFliesPayload {
  const day = session ?? sessionDateEt();
  const liveDb = path.join(config.paths.fliesDir, "live_trades.db");
  const paperDb = path.join(config.paths.fliesDir, "paper_trades.db");
  const lock = readLockStatus(config);
  const fliesCfg = readJson(config.paths.fliesConfig) ?? {};
  const liveCfg = (fliesCfg["live"] ?? {}) as Record<string, unknown>;
  const armName = str(liveCfg["arm"]);
  const symbol = str(liveCfg["symbol"]) ?? "SPX";
  const cap = num(liveCfg["max_open_margin_dollars"]);
  const loop = readFliesLoopStatus(config, "live");
  const analytics = readFliesAnalytics(config, "live", { arm: null, date: day, symbol: null, era: null });
  const feed: LiveFeedRow[] = readFliesJournal(config, "live", day, null).rows.map((r) => ({
    mode: r.mode,
    reason: r.reason,
    accepted: r.accepted,
    firstSeen: r.firstSeen,
    lastSeen: r.lastSeen,
    occurrences: r.occurrences,
    centerLast: r.centerLast,
    detail: r.detail,
  }));
  const bounds = periodBounds(day);
  const fliesModule = lock.modules.find((m) => m.id === "flies");

  const empty = (): Omit<LivePeriod, "paperNet"> => ({ net: 0, trades: 0, sessions: 0 });
  const ledger = readOnlyDb(liveDb, (db) => ({
    periods: {
      today: settledNet(db, bounds.today, armName),
      week: settledNet(db, bounds.week, armName),
      month: settledNet(db, bounds.month, armName),
      year: settledNet(db, bounds.year, armName),
    },
    positions: positionsFor(db, day),
    marks: markSeries(db, day),
    gaps: gapsFor(db, day),
  }));
  const paper = withReadOnlyDb(paperDb, null as null | Record<string, number>, (db) => ({
    today: settledNet(db, bounds.today, "control").net,
    week: settledNet(db, bounds.week, "control").net,
    month: settledNet(db, bounds.month, "control").net,
    year: settledNet(db, bounds.year, "control").net,
  }));

  const value = ledger.status === "ok" ? ledger.value : null;
  const period = (k: "today" | "week" | "month" | "year"): LivePeriod => ({
    ...(value?.periods[k] ?? empty()),
    paperNet: paper?.[k] ?? null,
  });
  const positions = value?.positions ?? [];
  const marks = value?.marks ?? { markPnl: [], openMargin: [], latest: null };
  const dd = drawdownOf(marks.markPnl);

  const trail = spotTrail(config, symbol, day);
  const baseline = prevClose(config, symbol, day);
  const base = baseline ?? (trail.length > 0 ? trail[0]!.spot : null);
  // Regular hours only. The gex recorder also samples off-hours, writing the frozen cached spot
  // -- a flat pre-open line at 0% that then ramps into the first real print. Clipped here so the
  // payload is the session, not the recorder's schedule.
  const spx: LivePoint[] =
    base !== null && base > 0
      ? trail
          .map((p) => ({ m: etMinuteOfEpoch(p.ts), v: ((p.spot - base) / base) * 100 }))
          .filter((p) => p.m >= RTH_OPEN_MIN && p.m <= RTH_CLOSE_MIN)
      : [];

  const broker = readBrokerAccount();
  const latestMargin = marks.openMargin.length > 0 ? marks.openMargin[marks.openMargin.length - 1]!.v : Math.abs(analytics.today.maxPossibleLoss);

  return {
    generatedAt: new Date().toISOString(),
    session: day,
    ledger: ledger.status,
    ledgerError: ledger.status === "failed" ? ledger.error : null,
    arm: {
      armed: lock.fliesArm.armed,
      date: lock.fliesArm.date,
      stale: lock.fliesArm.stale,
      halted: lock.halted,
      liveEnabled: fliesModule?.liveEnabled ?? null,
      arm: armName,
      symbol,
    },
    loop: { state: loop.state, ageSeconds: loop.ageSeconds, lastIterationAt: loop.lastIterationAt },
    periods: { today: period("today"), week: period("week"), month: period("month"), year: period("year") },
    today: {
      positions: positions.length,
      open: positions.filter((p) => p.status === "open").length,
      pending: positions.filter((p) => p.entryFillStatus === "pending" || p.completionFillStatus === "pending").length,
      completionPct: analytics.today.completionPct,
      fees: analytics.today.fees,
      maxPossibleLoss: analytics.today.maxPossibleLoss,
      markPnl: marks.latest?.total ?? null,
      markedAt: marks.latest?.at ?? null,
      maxDrawdown: dd.maxDrawdown,
      peakMarkPnl: dd.peak,
    },
    buyingPower: {
      open: latestMargin,
      cap,
      account: broker.account,
      accountError: broker.ok ? null : broker.error,
    },
    positions,
    feed,
    series: {
      spx,
      spxBaseline: base !== null ? { value: base, source: baseline !== null ? "prev_close" : "first_tick" } : null,
      vix: regimeSeries(config, "vix", day),
      vix3m: regimeSeries(config, "vix3m", day),
      markPnl: marks.markPnl,
      openMargin: marks.openMargin,
      gaps: value?.gaps ?? [],
    },
  };
}
