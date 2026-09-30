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
import type {
  LiveFeedRow,
  LiveFliesPayload,
  LiveGap,
  LiveOnRisk,
  LivePerformance,
  LivePeriod,
  LivePoint,
  LivePosition,
  LiveRiskSession,
} from "@console/shared";
import { PERFORMANCE_MODULE_SCHEMA } from "@console/shared";
import { readOnlyDb, withReadOnlyDb, num, str, readJson, suiteEra } from "./db.js";
import { readModuleMetrics } from "../services/metricsBridge.js";
import { readFliesAnalytics, readFliesJournal, readFliesLoopStatus, readFliesPerformance } from "./flies.js";
import { readLockStatus, sessionDateEt } from "../services/liveLock.js";
import { readBrokerAccount } from "../services/brokerBridge.js";

const ET = "America/New_York";
const RTH_OPEN_MIN = 9 * 60 + 30;
const RTH_CLOSE_MIN = 16 * 60;

// --------------------------------------------------------------------------- period bounds (ET)
function iso(y: number, m: number, d: number): string {
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** `{today, week, month, year}` as inclusive `[start, end]` ISO session-date bounds. The week
 *  starts Monday. Derived from the SESSION, never the clock: until 2026-09-22 this read the wall
 *  clock's week, so a past session opened on a later Monday got an empty window and the Live
 *  page's week/month/year figures were the current period's, whatever day was being read. */
export function periodBounds(session: string): Record<"today" | "week" | "month" | "year", [string, string]> {
  const [y, m, day] = session.split("-").map(Number) as [number, number, number];
  const dow = new Date(Date.UTC(y, m - 1, day)).getUTCDay();
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
function settledNet(db: Database.Database, bounds: [string, string], arm: string | null): Omit<LivePeriod, "paperNet" | "onRisk"> {
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

// --------------------------------------------------------------------------- which session
function hasTable(db: Database.Database, name: string): boolean {
  return db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(name) !== undefined;
}

/** Has `day`'s session opened? Evidence, not a calendar -- the same rule `sessionPeakWorst` holds
 *  its figure by: a `fly_snapshots` row dated `day` at or past 09:30 ET in either ledger (the paper
 *  loop runs every session; the live loop only when armed), or any live position dated `day`. */
function sessionOpened(liveDb: string, paperDb: string, day: string): boolean {
  const snapshotAfterOpen = (db: Database.Database): boolean =>
    hasTable(db, "fly_snapshots") &&
    db
      .prepare<[string], Record<string, unknown>>(
        "SELECT 1 FROM fly_snapshots WHERE trade_date = ? AND substr(iteration_ts, 12, 5) >= '09:30' LIMIT 1",
      )
      .get(day) !== undefined;
  const live = withReadOnlyDb(liveDb, false, (db) =>
    snapshotAfterOpen(db) || db.prepare<[string], unknown>("SELECT 1 FROM fly_positions WHERE trade_date = ? LIMIT 1").get(day) !== undefined,
  );
  return live || withReadOnlyDb(paperDb, false, snapshotAfterOpen);
}

/**
 * The session the page shows when none is asked for. Once today's session has opened, today. Before
 * that -- overnight, pre-open, a weekend or a holiday -- the last session the pilot settled, so the
 * page carries the numbers it just finished instead of an empty day that reads as nothing traded.
 */
export function resolveLiveSession(
  config: ConsoleConfig,
  requested: string | null,
  today: string = sessionDateEt(),
): { session: string; basis: LiveFliesPayload["sessionBasis"] } {
  if (requested !== null) return { session: requested, basis: "requested" };
  const liveDb = path.join(config.paths.fliesDir, "live_trades.db");
  const paperDb = path.join(config.paths.fliesDir, "paper_trades.db");
  if (sessionOpened(liveDb, paperDb, today)) return { session: today, basis: "today" };
  const last = withReadOnlyDb<string | null>(liveDb, null, (db) =>
    str(
      db
        .prepare<[string], Record<string, unknown>>(
          "SELECT MAX(trade_date) AS d FROM fly_positions WHERE status = 'settled' AND trade_date < ?",
        )
        .get(today)?.["d"],
    ),
  );
  return last !== null ? { session: last, basis: "last_completed" } : { session: today, basis: "today" };
}

// --------------------------------------------------------------------------- return on session peak risk
/**
 * Every session the arm has rows for, newest first: settled net (the `settledNet` rule) against
 * the day's largest `fly_live_marks.open_margin`. The marks table is the loop's whole book, not one
 * arm's -- the pilot runs one arm a day, and a session only appears here when this arm traded it.
 */
export function riskSessions(db: Database.Database, arm: string | null): LiveRiskSession[] {
  const armClause = arm !== null ? " AND arm = ?" : "";
  const armParams: string[] = arm !== null ? [arm] : [];
  const days = new Map<string, LiveRiskSession>();
  const at = (d: string): LiveRiskSession => {
    let s = days.get(d);
    if (s === undefined) {
      s = { session: d, trades: 0, net: 0, peakRisk: null, peakAt: null, onRisk: null, complete: true };
      days.set(d, s);
    }
    return s;
  };
  for (const r of db
    .prepare<string[], Record<string, unknown>>(
      `SELECT trade_date, COUNT(*) AS trades, SUM(COALESCE(gross_pnl, 0) - COALESCE(fees, 0)) AS net
         FROM fly_positions WHERE status = 'settled'${armClause} GROUP BY trade_date`,
    )
    .all(...armParams)) {
    const s = at(String(r["trade_date"] ?? ""));
    s.trades = Number(r["trades"] ?? 0);
    s.net = Number(r["net"] ?? 0);
  }
  for (const r of db
    .prepare<string[], Record<string, unknown>>(
      `SELECT DISTINCT trade_date FROM fly_positions
        WHERE COALESCE(status, '') NOT IN ('settled', 'closed', 'cancelled', 'voided')${armClause}`,
    )
    .all(...armParams)) {
    at(String(r["trade_date"] ?? "")).complete = false;
  }
  if (hasTable(db, "fly_live_marks")) {
    // One row per day: SQLite returns the bare `iteration_ts` from the row MAX() chose.
    for (const r of db
      .prepare<[], Record<string, unknown>>(
        "SELECT trade_date, iteration_ts, MAX(open_margin) AS peak FROM fly_live_marks GROUP BY trade_date",
      )
      .all()) {
      const s = days.get(String(r["trade_date"] ?? ""));
      const peak = num(r["peak"]);
      if (s === undefined || peak === null || !(peak > 0)) continue;
      s.peakRisk = peak;
      s.peakAt = str(r["iteration_ts"]);
    }
  }
  const out = [...days.values()].filter((s) => s.session !== "");
  for (const s of out) s.onRisk = s.complete && s.peakRisk !== null ? s.net / s.peakRisk : null;
  return out.sort((a, b) => b.session.localeCompare(a.session));
}

/** Σ net / Σ peak over the finished sessions in `bounds` that recorded a peak. The numerator is
 *  matched to the denominator: a session with no recorded peak is counted in `of`, never in either. */
export function onRiskOver(sessions: LiveRiskSession[], bounds: [string, string] | null = null): LiveOnRisk {
  const inScope = sessions.filter((s) => s.trades > 0 && (bounds === null || (s.session >= bounds[0] && s.session <= bounds[1])));
  const covered = inScope.filter((s) => s.complete && s.peakRisk !== null);
  const net = covered.reduce((a, s) => a + s.net, 0);
  const peakRisk = covered.reduce((a, s) => a + (s.peakRisk ?? 0), 0);
  return { net, peakRisk, ratio: peakRisk > 0 ? net / peakRisk : null, sessions: covered.length, of: inScope.length };
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
        netCredit: Number(r["net"] ?? 0),
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

// --------------------------------------------------------------------------- performance
/** The flies completion tab's live-mode read (`readFliesPerformance`) and the performance slide's
 *  calibration reading, both for the pilot's arm in their own default scopes -- the whole live
 *  record, not bounded by `?session` -- carried, not recomputed. */
function livePerformance(config: ConsoleConfig, arm: string | null, sessions: LiveRiskSession[]): LivePerformance {
  const p = readFliesPerformance(config, "live", "daily", { arm, date: null, symbol: null, era: null });
  const from = suiteEra(config.paths.orchestratorConfig).from;
  // Memoised in the bridge (120 s), so the page's 15 s poll does not spawn a process per poll.
  const metrics = readModuleMetrics(path.join(config.paths.fliesDir, "live_trades.db"), PERFORMANCE_MODULE_SCHEMA.flies, from, null);
  const group = arm !== null ? metrics.metrics?.groups[arm] : undefined;
  return {
    arm,
    tiles: {
      trades: p.tiles.trades,
      sessions: p.tiles.sessions,
      netPnl: p.tiles.netPnl,
      winRatePct: p.tiles.winRatePct,
      profitFactor: p.tiles.profitFactor,
      feeDragPct: p.tiles.feeDragPct,
      completionRatePct: p.tiles.completionRatePct,
    },
    risk: p.risk,
    maxDrawdown: p.equity.length > 0 ? Math.max(...p.equity.map((e) => e.drawdown), 0) : null,
    equity: p.equity,
    completion: p.completion,
    completionTrend: p.completionTrend,
    liveVsPaper: p.liveVsPaper,
    onRisk: onRiskOver(sessions),
    sessions,
    calibration: { reading: group?.reading ?? null, from, error: metrics.ok ? null : metrics.error },
  };
}

// --------------------------------------------------------------------------- the payload
export function readFliesLive(config: ConsoleConfig, session: string | null = null): LiveFliesPayload {
  const resolved = resolveLiveSession(config, session);
  const day = resolved.session;
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

  const empty = (): Omit<LivePeriod, "paperNet" | "onRisk"> => ({ net: 0, trades: 0, sessions: 0 });
  const ledger = readOnlyDb(liveDb, (db) => ({
    risk: riskSessions(db, armName),
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
  const risk = value?.risk ?? [];
  const period = (k: "today" | "week" | "month" | "year"): LivePeriod => ({
    ...(value?.periods[k] ?? empty()),
    paperNet: paper?.[k] ?? null,
    onRisk: onRiskOver(risk, bounds[k]),
  });
  const performance = livePerformance(config, armName, risk);
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
    sessionBasis: resolved.basis,
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
      sessionPeakWorst: analytics.today.sessionPeakWorst,
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
    performance,
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
