/**
 * The Live page's `today` tab (2026-10-15): every live loop's day on one payload -- flies, MEIC and
 * bwb -- composed from the readers that already serve them. Nothing here is a new money rule:
 *
 * - **flies**: the arm record and halt flag are `services/liveLock`, the loop pill
 *   `readFliesLoopStatus`, today's positions and mark `fliesLive.ts`'s `positionsFor` /
 *   `markSeries`, the worst case `readFliesAnalytics` (each open position's floor after fees), and
 *   today's money the settled trade log (`readFliesTradeLog`, the flies trade standard's columns).
 * - **meic**: `readMeicLive` whole, today's money its own `totalsOf` over today's resolved rows.
 * - **bwb**: `readBwb` / `readBwbHistory` in live mode (the bwb trade standard), with
 *   `readers/liveBooks.ts`'s bwb read for the ledger's present / absent / failed status, since
 *   `readBwb` falls back silently.
 * - the orphan, hold and broker-health files beside each live ledger are `core.execution`'s three
 *   files in every loop, read by `meicLive.ts`'s readers.
 *
 * Three small mirrors are new, each a module's own status query (`live_loop.py`), named where it
 * sits: flies' and MEIC's `daily_loss_tripped`, and flies' `overdue_settlement`. bwb's breaker
 * input IS today's settled net (`db.settled_net_for_session`), so it is read off the composed
 * money rather than queried again.
 *
 * The suite line totals each loop's realised net for today. All three state it the same way --
 * resolved positions at the broker's fills, less trading fees and settlement, slippage never
 * charged again -- so those sum. The worst case and the mark are each loop's own measure (a fly's
 * floor after fees, a BWB's entry max loss; a fly's mid mark, a BWB's unrealised net), so they are
 * shown per loop and never totalled. A loop whose ledger read failed makes the total n/r: a total
 * missing one loop reads as the whole suite's.
 *
 * Read-only, files and SQLite only; nothing here touches an order or spawns a process.
 */
import path from "node:path";
import type Database from "better-sqlite3";
import { sumIfAllRecorded } from "@console/shared";
import type {
  LiveAttentionItem,
  LiveLoopId,
  LiveMeicBrokerHealth,
  LiveTodayArm,
  LiveTodayLoop,
  LiveTodayMoney,
  LiveTodayOverdue,
  LiveTodayPayload,
  LiveTodaySuite,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { hasTable, num, readJson, readOnlyDb, str } from "./db.js";
import { NO_TRADE_LOG_QUERY, readFliesAnalytics, readFliesLoopStatus, readFliesTradeLog } from "./flies.js";
import { markSeries, positionsFor } from "./fliesLive.js";
import { DEFAULT_DISARM, etParts, minutesOf, readBrokerHealth, readHeld, readMeicLive, readOrphans, totalsOf } from "./meicLive.js";
import { readBwb, readBwbHistory } from "./bwb.js";
import { LIVE_BOOKS } from "./liveBooks.js";
import { readLockStatus, readModuleGate } from "../services/liveLock.js";
import { MAX_PAGE } from "./paging.js";

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

function liveBlock(doc: Record<string, unknown> | null): Record<string, unknown> {
  const live = doc?.["live"];
  return typeof live === "object" && live !== null && !Array.isArray(live) ? (live as Record<string, unknown>) : {};
}

/** The arm record's own fields, measured against `session` / `minute` (the composer's clock, not the wall's). */
function armFrom(
  record: Record<string, unknown> | null,
  session: string,
  minute: number,
  live: Record<string, unknown>,
  liveEnabled: boolean | null,
  symbol: string | null,
): LiveTodayArm {
  const date = str(record?.["date"]);
  const disarmTime = str(live["disarm_time"]) ?? DEFAULT_DISARM;
  const disarmMin = minutesOf(disarmTime);
  return {
    armed: date === session,
    date,
    at: str(record?.["at"]),
    stale: date !== null && date !== session,
    disarmTime,
    pastDisarm: disarmMin !== null && minute >= disarmMin,
    liveEnabled,
    arm: str(live["arm"]),
    symbol,
  };
}

function heldCount(dir: string): number {
  const h = readHeld(dir);
  return h.unresolved.length + h.unrecorded.length;
}

function moneyOf(
  rows: Array<{ gross: number | null; fees: number | null; settlementFees: number | null }>,
  basis: string,
): LiveTodayMoney {
  let gross = 0;
  let fees = 0;
  let settlement = 0;
  let settlementTrades = 0;
  for (const r of rows) {
    gross += r.gross ?? 0;
    fees += r.fees ?? 0;
    if (r.settlementFees !== null) {
      settlement += r.settlementFees;
      settlementTrades += 1;
    }
  }
  return {
    trades: rows.length,
    gross: round2(gross),
    fees: round2(fees),
    settlementFees: sumIfAllRecorded(round2(settlement), settlementTrades, rows.length),
    settlementRecorded: round2(settlement),
    settlementTrades,
    // gross − trading fees − settlement: the ledger's own `gross_pnl - fees`, since trading fees
    // are the fee total less the settlement recorded apart (and the whole total where it was not).
    net: round2(gross - fees - settlement),
    basis,
  };
}

function breakerOf(limit: number | null, figure: number | null, basis: string): LiveTodayLoop["breaker"] {
  if (!limit) return { limit: null, figure, tripped: null, basis };
  return { limit, figure, tripped: figure === null ? null : figure <= -Math.abs(limit), basis };
}

// --------------------------------------------------------------------------- flies
/** flies `live_loop.daily_loss_tripped`'s input: settled `pnl` for the day, every arm. */
function fliesBreakerFigure(db: Database.Database, session: string): number {
  const r = db
    .prepare<[string], { v: number | null }>(
      "SELECT COALESCE(SUM(pnl), 0) AS v FROM fly_positions WHERE trade_date = ? AND status = 'settled'",
    )
    .get(session);
  return Number(r?.v ?? 0);
}

/** flies `live_loop.overdue_settlement`: past sessions the live ledger still holds open. */
function fliesOverdue(db: Database.Database, session: string): LiveTodayOverdue[] {
  return db
    .prepare<[string], Record<string, unknown>>(
      "SELECT trade_date, COUNT(*) AS n, SUM(entry_fill_status = 'pending') AS pend FROM fly_positions " +
        "WHERE status = 'open' AND trade_date < ? GROUP BY trade_date ORDER BY trade_date",
    )
    .all(session)
    .map((r) => ({ session: str(r["trade_date"]) ?? "", positions: Number(r["n"] ?? 0), pendingEntries: Number(r["pend"] ?? 0) }));
}

function readFliesToday(config: ConsoleConfig, session: string, minute: number): LiveTodayLoop {
  const dbPath = path.join(config.paths.fliesDir, "live_trades.db");
  const dir = path.dirname(dbPath);
  const fliesCfg = readJson(config.paths.fliesConfig);
  const live = liveBlock(fliesCfg);
  // The record `services/liveLock` reads, measured against this payload's clock rather than the wall's.
  const record = readJson(path.join(config.paths.cherrypick, "state", "flies-live-arm.json"));
  const arm = armFrom(record, session, minute, live, readModuleGate(config, "flies").liveEnabled, str(live["symbol"]) ?? "SPX");

  const read = readOnlyDb(dbPath, (db) => {
    if (!hasTable(db, "fly_positions")) {
      return { positions: [], marks: { markPnl: [], openMargin: [], latest: null }, breaker: 0, overdue: [] as LiveTodayOverdue[] };
    }
    return {
      positions: positionsFor(db, session),
      marks: markSeries(db, session),
      breaker: fliesBreakerFigure(db, session),
      overdue: fliesOverdue(db, session),
    };
  });
  const loopStatus = readFliesLoopStatus(config, "live");
  const base = {
    loop: "flies" as const,
    to: "/live/flies",
    ledger: read.status,
    ledgerError: read.status === "failed" ? read.error : null,
    arm,
    loopState: { state: loopStatus.state, lastAt: loopStatus.lastIterationAt, ageSeconds: loopStatus.ageSeconds },
    workingBasis: "entry and completion orders working",
    worstCaseBasis: "each open fly's floor after fees, summed (the flies analytics' max possible loss)",
    orphans: (() => {
      const o = readOrphans(dir);
      return { at: o.at, count: o.orders.length };
    })(),
    held: heldCount(dir),
    brokerHealth: readBrokerHealth(dir),
  };
  const limit = num(live["daily_loss_halt_dollars"]);
  const breakerBasis = "settled pnl today, every arm (live_loop.daily_loss_tripped)";
  if (read.status !== "ok") {
    return {
      ...base,
      open: 0,
      working: 0,
      worstCase: null,
      entriesToday: 0,
      today: null,
      mark: null,
      breaker: breakerOf(limit, null, breakerBasis),
      overdue: [],
    };
  }
  const v = read.value;
  const positions = v.positions.filter((p) => p.status !== "cancelled");
  const pendingEntries = positions.filter((p) => p.entryFillStatus === "pending").length;
  const openNow =
    positions.filter((p) => p.status === "open" && p.entryFillStatus !== "pending").length +
    v.overdue.reduce((s, o) => s + o.positions - o.pendingEntries, 0);
  const working = positions.filter(
    (p) => p.status === "open" && (p.entryFillStatus === "pending" || p.completionFillStatus === "pending"),
  ).length;
  // The analytics' position count is the one that leaves voided rows out (`positionsFor` keeps a
  // voided row, which stays `settled`); less the entries still working, it is today's fills.
  const analytics = readFliesAnalytics(config, "live", { arm: null, date: session, symbol: null, era: "ALL" });
  const log = readFliesTradeLog(config, "live", {
    ...NO_TRADE_LOG_QUERY,
    era: "ALL",
    from: session,
    to: session,
    limit: MAX_PAGE,
  });
  const money = moneyOf(log.rows, "positions settled today, voided rows out (the flies trade log)");
  return {
    ...base,
    open: openNow,
    working,
    worstCase: round2(analytics.today.maxPossibleLoss),
    entriesToday: Math.max(0, analytics.today.positions - pendingEntries),
    // A day of more fills than one page holds cannot be summed off the page; say so rather than undercount.
    today: log.total > log.rows.length ? null : money,
    mark: {
      value: v.marks.latest?.total ?? null,
      at: v.marks.latest?.at ?? null,
      basis: "the loop's own mark at mid over today's positions (fly_live_marks), not a fill",
    },
    breaker: breakerOf(limit, round2(v.breaker), breakerBasis),
    overdue: v.overdue,
  };
}

// --------------------------------------------------------------------------- meic
/** MEIC `live_loop.daily_loss_tripped`'s input: today's `pnl` over every status, open ICs included. */
function meicBreakerFigure(dbPath: string, session: string): number | null {
  const r = readOnlyDb(dbPath, (db) =>
    hasTable(db, "ic_trades")
      ? Number(
          db.prepare<[string], { v: number | null }>("SELECT COALESCE(SUM(pnl), 0) AS v FROM ic_trades WHERE trade_date = ?").get(session)?.v ?? 0,
        )
      : null,
  );
  return r.status === "ok" ? r.value : null;
}

function readMeicToday(config: ConsoleConfig, now: Date): LiveTodayLoop {
  const p = readMeicLive(config, now);
  const session = p.session;
  const meicCfg = readJson(path.join(config.paths.cherrypick, "config", "meic.json"));
  const live = liveBlock(meicCfg);
  const dbPath = path.join(config.paths.meicDir, "meic_trades.db");
  const arm: LiveTodayArm = {
    armed: p.arm.armed,
    date: p.arm.date,
    at: p.arm.at,
    stale: p.arm.stale,
    disarmTime: p.arm.disarmTime,
    pastDisarm: p.arm.pastDisarm,
    liveEnabled: p.arm.liveEnabled,
    arm: str(live["arm"]),
    symbol: p.arm.symbol,
  };
  const todays = p.history.filter((r) => r.tradeDate === session);
  const t = totalsOf(todays);
  const today: LiveTodayMoney | null =
    p.ledger === "ok"
      ? {
          trades: t.trades,
          gross: t.gross,
          fees: t.fees,
          settlementFees: t.settlementFees,
          settlementRecorded: t.settlementRecorded,
          settlementTrades: t.settlementTrades,
          net: t.net,
          basis: "iron condors resolved today (stopped, expired, force-closed); an IC still open with one side stopped is not in it",
        }
      : null;
  const overdueBySession = new Map<string, LiveTodayOverdue>();
  for (const r of p.open.filter((o) => o.overdue)) {
    const o = overdueBySession.get(r.tradeDate) ?? { session: r.tradeDate, positions: 0, pendingEntries: 0 };
    if (r.working) o.pendingEntries += 1;
    else o.positions += 1;
    overdueBySession.set(r.tradeDate, o);
  }
  const limit = num(live["daily_loss_halt_dollars"]);
  return {
    loop: "meic",
    to: "/live/meic",
    ledger: p.ledger,
    ledgerError: p.ledgerError,
    arm,
    loopState: null,
    open: p.open.filter((r) => !r.working).length,
    working: p.open.filter((r) => r.working).length + p.open.filter((r) => r.putClosing).length + p.open.filter((r) => r.callClosing).length,
    workingBasis: "entry orders working, plus each side with a close order working",
    worstCase: null,
    worstCaseBasis: "the MEIC live reader records no worst case per IC",
    entriesToday:
      p.open.filter((r) => r.tradeDate === session && !r.working).length + todays.length,
    today,
    mark: { value: null, at: null, basis: "MEIC's live loop writes no marks" },
    breaker: breakerOf(
      limit,
      limit ? meicBreakerFigure(dbPath, session) : null,
      "today's pnl over every status, open ICs included (live_loop.daily_loss_tripped)",
    ),
    orphans: { at: p.orphans.at, count: p.orphans.orders.length },
    held: p.held.unresolved.length + p.held.unrecorded.length,
    brokerHealth: p.brokerHealth,
    overdue: [...overdueBySession.values()].sort((a, b) => a.session.localeCompare(b.session)),
  };
}

// --------------------------------------------------------------------------- bwb
function readBwbToday(config: ConsoleConfig, session: string, minute: number): LiveTodayLoop {
  const dbPath = path.join(config.paths.bwbDir, "live_trades.db");
  const dir = path.dirname(dbPath);
  const bwbCfg = readJson(path.join(config.paths.cherrypick, "config", "bwb.json"));
  const live = liveBlock(bwbCfg);
  const record = readJson(path.join(config.paths.cherrypick, "state", "bwb-live-arm.json"));
  const symbol = (str(bwbCfg?.["symbol"]) ?? "SPX").trim().toUpperCase();
  const arm = armFrom(record, session, minute, live, readModuleGate(config, "bwb").liveEnabled, symbol);
  // The Overview's own bwb read, for the status `readBwb` cannot report (it falls back silently).
  const spec = LIVE_BOOKS.find((b) => b.module === "bwb")!;
  const status = readOnlyDb(dbPath, spec.read);
  const limit = num(live["daily_loss_halt_dollars"]);
  const breakerBasis = "settled net today, gross − fees over positions closed today (db.settled_net_for_session)";
  const base = {
    loop: "bwb" as const,
    to: "/bwb/livegap",
    ledger: status.status,
    ledgerError: status.status === "failed" ? status.error : null,
    arm,
    workingBasis: "entry orders working",
    worstCaseBasis: "each open position's entry max loss × 100 × quantity, before fees (bwb's margin-cap figure)",
    orphans: (() => {
      const o = readOrphans(dir);
      return { at: o.at, count: o.orders.length };
    })(),
    held: heldCount(dir),
    brokerHealth: readBrokerHealth(dir),
  };
  if (status.status !== "ok") {
    return {
      ...base,
      loopState: null,
      open: 0,
      working: 0,
      worstCase: null,
      entriesToday: 0,
      today: null,
      mark: null,
      breaker: breakerOf(limit, null, breakerBasis),
      overdue: [],
    };
  }
  const b = readBwb(config, "live");
  const hist = readBwbHistory(config, { arm: null, symbol: null, range: { from: session, to: session } }, { limit: MAX_PAGE, offset: 0 }, "live");
  const held = b.openPositions.filter((p) => p.status !== "pending");
  const pending = b.openPositions.filter((p) => p.status === "pending");
  const worstParts = held.map((p) => (p.entryMaxLoss === null ? null : p.entryMaxLoss * 100 * (p.quantity ?? 1)));
  const markParts = held.map((p) => p.unrealisedNet);
  const overdue = new Map<string, LiveTodayOverdue>();
  for (const p of held) {
    const exp = (p.expiration ?? "").slice(0, 10);
    if (exp !== "" && exp < session && (p.status === "open" || p.status === "short_settled")) {
      const o = overdue.get(exp) ?? { session: exp, positions: 0, pendingEntries: 0 };
      o.positions += 1;
      overdue.set(exp, o);
    }
  }
  for (const p of pending) {
    if (p.entrySession !== "" && p.entrySession < session) {
      const o = overdue.get(p.entrySession) ?? { session: p.entrySession, positions: 0, pendingEntries: 0 };
      o.pendingEntries += 1;
      overdue.set(p.entrySession, o);
    }
  }
  const money = moneyOf(
    hist.rows.map((r) => ({ gross: r.grossPnl, fees: r.fees, settlementFees: r.settlementFees })),
    "positions closed today (by close session), gross − fees, the bwb trade standard",
  );
  const iteration = b.today.lastIteration;
  return {
    ...base,
    loopState:
      iteration === null
        ? { state: "no-data", lastAt: null, ageSeconds: null }
        : {
            state: iteration.ageSeconds < 120 ? "live" : "idle",
            lastAt: new Date(iteration.ranAt * 1000).toISOString(),
            ageSeconds: iteration.ageSeconds,
          },
    open: held.length,
    working: pending.length,
    worstCase: held.length === 0 ? 0 : worstParts.some((w) => w === null) ? null : -round2(worstParts.reduce<number>((s, w) => s + (w ?? 0), 0)),
    entriesToday:
      held.filter((p) => p.entrySession === session).length + hist.rows.filter((r) => r.entrySession === session).length,
    today: hist.total > hist.rows.length ? null : money,
    mark: {
      value: held.length === 0 ? null : markParts.some((m) => m === null) ? null : round2(markParts.reduce<number>((s, m) => s + (m ?? 0), 0)),
      at: null,
      basis: "unrealised net over the open positions: the latest usable mark at mid, less costs to date; n/r when any is unmarked",
    },
    // bwb's breaker input is exactly today's settled net, which `money` already is.
    breaker: breakerOf(limit, money.net, breakerBasis),
    overdue: [...overdue.values()].sort((a, c) => a.session.localeCompare(c.session)),
  };
}

// --------------------------------------------------------------------------- attention and suite
function attentionFor(l: LiveTodayLoop): LiveAttentionItem[] {
  const out: LiveAttentionItem[] = [];
  const add = (kind: LiveAttentionItem["kind"], detail: string) => out.push({ loop: l.loop, kind, detail, to: l.to });
  if (l.ledger === "failed") add("ledger_unreadable", `the live ledger could not be read: ${l.ledgerError ?? "unknown error"}`);
  if (l.orphans.count > 0)
    add("orphaned_orders", `${l.orphans.count} working broker order${l.orphans.count === 1 ? "" : "s"} no live ledger knows (sweep ${l.orphans.at ?? "time unknown"})`);
  if (l.held > 0) add("held_submission", `${l.held} held submission${l.held === 1 ? "" : "s"}: no broker answer, or a fill the ledger never recorded`);
  if (l.breaker.tripped === true)
    add("daily_loss_breaker", `daily-loss breaker tripped: ${l.breaker.figure ?? "?"} against a ${l.breaker.limit ?? "?"} limit; no new entries today`);
  for (const o of l.overdue) {
    const parts = [
      o.positions > 0 ? `${o.positions} position${o.positions === 1 ? "" : "s"} still open` : null,
      o.pendingEntries > 0 ? `${o.pendingEntries} entr${o.pendingEntries === 1 ? "y" : "ies"} still pending` : null,
    ].filter((s) => s !== null);
    add("overdue_settlement", `${o.session}: ${parts.join(", ")} -- settlement overdue`);
  }
  const h: LiveMeicBrokerHealth | null = l.brokerHealth;
  if (h !== null && h.failingSince !== null)
    add("broker_failing", `broker calls failing since ${h.failingSince} (×${h.failures})${h.lastError !== null ? `: ${h.lastError}` : ""}`);
  return out;
}

export function suiteOf(loops: LiveTodayLoop[]): LiveTodaySuite {
  const excluded: LiveTodaySuite["excluded"] = [];
  const summed: LiveLoopId[] = [];
  let failed = false;
  for (const l of loops) {
    if (l.ledger === "absent") excluded.push({ loop: l.loop, reason: "no live ledger on this machine" });
    else if (l.ledger === "failed" || l.today === null) {
      failed = true;
      excluded.push({ loop: l.loop, reason: l.ledger === "failed" ? "the live ledger could not be read" : "today's rows could not all be read" });
    } else summed.push(l.loop);
  }
  const armed = loops.filter((l) => l.arm.armed && !l.arm.pastDisarm).map((l) => l.loop);
  // A loop unread makes the total n/r; so does nothing read at all -- no ledger anywhere is not a flat day.
  if (failed || summed.length === 0) return { loops: loops.length, armed, net: null, gross: null, fees: null, settlementFees: null, trades: null, summed: [], excluded };
  const money = loops.filter((l) => summed.includes(l.loop)).map((l) => l.today!);
  const sum = (f: (m: LiveTodayMoney) => number) => round2(money.reduce((s, m) => s + f(m), 0));
  return {
    loops: loops.length,
    armed,
    net: sum((m) => m.net),
    gross: sum((m) => m.gross),
    fees: sum((m) => m.fees),
    settlementFees: money.some((m) => m.settlementFees === null) ? null : sum((m) => m.settlementFees ?? 0),
    trades: money.reduce((s, m) => s + m.trades, 0),
    summed,
    excluded,
  };
}

export function readLiveToday(config: ConsoleConfig, now: Date = new Date()): LiveTodayPayload {
  const { session, minute } = etParts(now);
  const loops = [readFliesToday(config, session, minute), readMeicToday(config, now), readBwbToday(config, session, minute)];
  return {
    generatedAt: now.toISOString(),
    session,
    halted: readLockStatus(config).halted,
    loops,
    attention: loops.flatMap(attentionFor),
    suite: suiteOf(loops),
  };
}
