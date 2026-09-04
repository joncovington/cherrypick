import path from "node:path";
import type { DeskPayload, DeskLiveness, DeskExposureRow, DeskEntriesRow, DeskEvidenceRow, DeskEodRow } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, readJson } from "./db.js";
import { streamerFreshness } from "./streamcache.js";
import { readMeicLoopStatus, readMeicOpenExposure } from "./meic.js";
import { readFliesLoopStatus, readFliesAnalytics } from "./flies.js";
import { readEntryAttempts } from "./attempts.js";
import { readPmcc, resolvePmccSession } from "./pmcc.js";
import { readCurve, resolveCurveSession } from "./curve.js";
import { readBwb } from "./bwb.js";
import { readCalendars, readCalendarsEntryAttempts } from "./calendars.js";
import { readEarningsDetail } from "./earnings.js";
import { buildSuiteReport, readFactSet } from "../services/report.js";
import { sessionDateEt } from "../services/liveLock.js";
import { readScreenMetrics } from "../services/screenBridge.js";

/**
 * The Overview's suite matrix, in one composed read.
 *
 * Deliberately reuses the readers each module's own page already calls (readPmcc/readCurve/
 * readBwb/readCalendars, readEntryAttempts, buildSuiteReport) rather than opening a second set of
 * queries against the same stores -- this is a COMPOSITION, not a second opinion. The one genuinely
 * new read is cadence: no reader here read a producer's declared tick interval against its own
 * config before, and it is deliberately the one thing that can come back `null` ("cadence
 * unknown") rather than a guessed threshold.
 */

/** `jobs.<module>.paper.tick_interval_seconds` from the orchestrator's own deployed config -- the
 *  one place a module's supervised cadence is declared. `null` when the config, the module's job
 *  entry, or the field itself is missing; a producer whose cadence cannot be read renders its age
 *  with no threshold rather than a false green. */
function moduleCadenceSeconds(orchestratorConfigPath: string, moduleId: string): number | null {
  const raw = readJson(orchestratorConfigPath);
  if (raw === null) return null;
  const modules = raw["modules"];
  if (modules === null || typeof modules !== "object") return null;
  const mod = (modules as Record<string, unknown>)[moduleId];
  if (mod === null || typeof mod !== "object") return null;
  const paper = (mod as Record<string, unknown>)["paper"];
  if (paper === null || typeof paper !== "object") return null;
  const v = (paper as Record<string, unknown>)["tick_interval_seconds"];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function liveness(
  id: string,
  label: string,
  kind: DeskLiveness["kind"],
  ageSeconds: number | null,
  cadenceSeconds: number | null,
): DeskLiveness {
  const overBy =
    ageSeconds !== null && cadenceSeconds !== null && ageSeconds > cadenceSeconds ? ageSeconds - cadenceSeconds : null;
  return { id, label, kind, ageSeconds, cadenceSeconds, overBy };
}

interface EarningsLoop {
  ranAt: number | null;
  ageSeconds: number | null;
}

/** Earnings has no dedicated loop-status reader (unlike meic/flies) -- this mirrors their shape
 *  directly off `loop_iterations`, whose `ran_at` is a REAL epoch-seconds column, not the ISO-ish
 *  strings meic/flies write. */
function readEarningsLoop(config: ConsoleConfig): EarningsLoop {
  const dbPath = path.join(config.paths.earningsDir, "paper_trades.db");
  return withReadOnlyDb<EarningsLoop>(dbPath, { ranAt: null, ageSeconds: null }, (db) => {
    const r = db.prepare<[], { ran_at: number }>("SELECT ran_at FROM loop_iterations ORDER BY id DESC LIMIT 1").get();
    if (r === undefined) return { ranAt: null, ageSeconds: null };
    return { ranAt: r.ran_at, ageSeconds: Math.max(0, Date.now() / 1000 - r.ran_at) };
  });
}

interface GexLoop {
  ageSeconds: number | null;
}

function readGexLoop(config: ConsoleConfig): GexLoop {
  const dbPath = path.join(config.paths.gexDir, "gex_history.db");
  return withReadOnlyDb<GexLoop>(dbPath, { ageSeconds: null }, (db) => {
    const r = db.prepare<[], { ts: number | string | null }>("SELECT MAX(ts) AS ts FROM gex_regime_history").get();
    if (r === undefined || r.ts === null) return { ageSeconds: null };
    // The recorder stores ts as epoch seconds, sometimes serialized as text -- see gex.ts's isoTs.
    const n = typeof r.ts === "number" ? r.ts : Number.parseFloat(r.ts);
    if (!Number.isFinite(n) || n <= 1e9) return { ageSeconds: null };
    return { ageSeconds: Math.max(0, Date.now() / 1000 - n) };
  });
}

function countOutcomes(
  rows: Array<{ outcome: string; blockDetail?: string | null }>,
): { filled: number; refused: number; noFill: number; topRefusal: string | null } {
  let filled = 0;
  let noFill = 0;
  const refusalCounts: Record<string, number> = {};
  for (const r of rows) {
    if (r.outcome === "filled") filled += 1;
    else if (r.outcome === "no_fill") noFill += 1;
    else {
      const key = r.blockDetail ?? r.outcome;
      refusalCounts[key] = (refusalCounts[key] ?? 0) + 1;
    }
  }
  const refused = Object.values(refusalCounts).reduce((s, n) => s + n, 0);
  const top = Object.entries(refusalCounts).sort((a, b) => b[1] - a[1])[0];
  return { filled, refused, noFill, topRefusal: top !== undefined ? `${top[0]} ×${String(top[1])}` : null };
}

function sumMaxLoss(positions: Array<{ entryMaxLoss?: number | null }>): number | null {
  return positions.reduce<number | null>((s, p) => (p.entryMaxLoss != null ? (s ?? 0) + p.entryMaxLoss : s), null);
}

function sumUnrealised(positions: Array<{ unrealisedNet?: number | null }>): number | null {
  return positions.reduce<number | null>((s, p) => (p.unrealisedNet != null ? (s ?? 0) + p.unrealisedNet : s), null);
}

function sumField(positions: Array<Record<string, unknown>>, field: string): number | null {
  return positions.reduce<number | null>((s, p) => {
    const v = p[field];
    return typeof v === "number" ? (s ?? 0) + v : s;
  }, null);
}

export function readDesk(config: ConsoleConfig): DeskPayload {
  const orch = config.paths.orchestratorConfig;
  const streamer = streamerFreshness(config);
  const meicLoop = readMeicLoopStatus(config, "paper");
  const fliesLoop = readFliesLoopStatus(config, "paper");
  const earningsLoop = readEarningsLoop(config);
  const gexLoop = readGexLoop(config);
  const pmcc = readPmcc(config);
  const curve = readCurve(config);
  const bwb = readBwb(config);
  const calendars = readCalendars(config);
  // "today" scope: no arm/date/symbol filter, current era -- the same shape every other module's
  // exposure row already reads, and the same aggregate FliesLightbox's own "now" tab already shows
  // as "max possible loss" / EarningsLightbox's "overview" tab shows as "capital at risk (open)".
  const fliesAnalytics = readFliesAnalytics(config, "paper", { arm: null, date: null, symbol: null, era: null });
  const meicExposure = readMeicOpenExposure(config, "paper");
  const earningsDetail = readEarningsDetail(config, "paper", null);

  const livenessRows: DeskLiveness[] = [
    liveness("streamer", "streamer", "streamer", streamer.ageSeconds, null),
    liveness("meic", "meic", "loop", meicLoop.ageSeconds, moduleCadenceSeconds(orch, "meic")),
    liveness("flies", "flies", "loop", fliesLoop.ageSeconds, moduleCadenceSeconds(orch, "flies")),
    liveness("earnings", "earnings", "loop", earningsLoop.ageSeconds, moduleCadenceSeconds(orch, "earnings")),
    liveness(
      "calendars",
      "calendars",
      "loop",
      calendars.today.lastIteration?.ageSeconds ?? null,
      moduleCadenceSeconds(orch, "calendars"),
    ),
    liveness("pmcc", "pmcc", "loop", pmcc.today.lastIteration?.ageSeconds ?? null, moduleCadenceSeconds(orch, "pmcc")),
    liveness("curve", "curve", "loop", curve.today.lastIteration?.ageSeconds ?? null, moduleCadenceSeconds(orch, "curve")),
    liveness("bwb", "bwb", "loop", bwb.today.lastIteration?.ageSeconds ?? null, moduleCadenceSeconds(orch, "bwb")),
    liveness("gex", "gex", "recorder", gexLoop.ageSeconds, null),
  ];

  const exposure: DeskExposureRow[] = [
    {
      module: "meic",
      // Same formula core.ledgers._meic_closed's _capital() already validates for closed trades,
      // now also computed in Python (analytics.headline's open_capital_at_risk) and mirrored here
      // -- meic-mirror.test.ts checks the two agree.
      open: meicExposure.open,
      atRisk: meicExposure.capitalAtRisk,
      atRiskLabel: "capital at risk",
      unrealisedNet: null,
      markAgeSeconds: meicLoop.ageSeconds,
      available: true,
      note: null,
    },
    {
      module: "flies",
      open: fliesAnalytics.today.open,
      // flies' own maxPossibleLoss is a signed P&L floor (negative = a real loss, zero = nothing
      // open can still lose -- FliesLightbox's own "now" tab shows it that way, in red when
      // negative). Every other row's atRisk is a positive magnitude (a debit paid, a max-loss
      // sum), so this column takes the absolute value here rather than exposing flies as the one
      // row where "at risk" reads negative under a header every other row treats as an amount.
      atRisk: Math.abs(fliesAnalytics.today.maxPossibleLoss),
      atRiskLabel: "max possible loss",
      unrealisedNet: null,
      markAgeSeconds: fliesLoop.ageSeconds,
      available: true,
      note: null,
    },
    {
      module: "earnings",
      open: earningsDetail.openCount,
      atRisk: earningsDetail.capitalAtRisk,
      atRiskLabel: "capital at risk",
      unrealisedNet: null,
      markAgeSeconds: earningsLoop.ageSeconds,
      available: true,
      note: null,
    },
    {
      module: "calendars",
      open: calendars.openPositions.length,
      atRisk: sumField(calendars.openPositions as unknown as Record<string, unknown>[], "entryDebit"),
      atRiskLabel: "debit at risk",
      unrealisedNet: sumField(calendars.openPositions as unknown as Record<string, unknown>[], "unrealisedNet"),
      markAgeSeconds: calendars.today.lastIteration?.ageSeconds ?? null,
      available: true,
      note: null,
    },
    {
      module: "pmcc",
      open: pmcc.openCount,
      atRisk: sumField(pmcc.openPositions as unknown as Record<string, unknown>[], "netDebit"),
      atRiskLabel: "debit at risk",
      unrealisedNet: sumUnrealised(pmcc.openPositions),
      markAgeSeconds: pmcc.today.lastIteration?.ageSeconds ?? null,
      available: true,
      note: null,
    },
    {
      module: "curve",
      open: curve.openCount,
      atRisk: sumMaxLoss(curve.openPositions),
      atRiskLabel: "at risk",
      unrealisedNet: sumUnrealised(curve.openPositions),
      markAgeSeconds: curve.today.lastIteration?.ageSeconds ?? null,
      available: true,
      note: null,
    },
    {
      module: "bwb",
      open: bwb.openCount,
      atRisk: sumMaxLoss(bwb.openPositions),
      atRiskLabel: "at risk (zero-floor by design)",
      unrealisedNet: sumUnrealised(bwb.openPositions),
      markAgeSeconds: bwb.today.lastIteration?.ageSeconds ?? null,
      available: true,
      note: null,
    },
  ];

  const meicAttempts = readEntryAttempts(config, "meic", "paper", null);
  const fliesAttempts = readEntryAttempts(config, "flies", "paper", null);
  // Both resolve the module's own canonical session rather than readEntryAttempts' own
  // MAX(trade_date) guess -- the same pmcc-attempts-timeline incident this reader must not
  // reintroduce (a loop that ran and found nothing to evaluate vs. this table's own last row).
  const pmccAttempts = readEntryAttempts(config, "pmcc", "paper", resolvePmccSession(config));
  const curveAttempts = readEntryAttempts(config, "curve", "paper", resolveCurveSession(config));
  const meicCounts = countOutcomes(meicAttempts.timeline);
  const fliesCounts = countOutcomes(fliesAttempts.timeline);
  const pmccCounts = countOutcomes(pmccAttempts.timeline);
  const curveCounts = countOutcomes(curveAttempts.timeline);
  const calendarsCounts = countOutcomes(readCalendarsEntryAttempts(config, null).rows);
  const bwbCounts = countOutcomes(
    bwb.entryAttemptsToday.map((a: { outcome: string }) => ({ outcome: a.outcome, blockDetail: null })),
  );
  // earnings screens candidate SYMBOLS rather than evaluating entry ticks on an already-chosen
  // structure, so "filled/refused/no fill" maps onto its own vocabulary rather than the shared
  // attempts-timeline shape: filled = opened, refused = rejected (screened out), no fill = accepted
  // but not (yet) opened -- the SAME funnel the (now-removed) screening-funnel card read, scoped to
  // today alone via `since` = today's ET date rather than the era-wide window that card used.
  const todayEt = sessionDateEt();
  const earningsMetrics = readScreenMetrics("paper", todayEt).metrics;
  const earningsFunnel = earningsMetrics?.funnel ?? null;
  const earningsCounts = {
    filled: earningsFunnel?.opened ?? 0,
    refused: earningsFunnel?.rejected ?? 0,
    noFill: earningsFunnel !== null ? Math.max(0, earningsFunnel.accepted - earningsFunnel.opened) : 0,
    // screen_metrics' own ordering: sole-blocker count first, then total -- "the ones a threshold
    // change actually rescues" per that module's own rule, not just the most frequent gate name.
    topRefusal: earningsMetrics?.reasons[0]?.reason ?? null,
  };

  const entries: DeskEntriesRow[] = [
    { module: "meic", ...meicCounts, sessionNet: null, available: true, note: null },
    { module: "flies", ...fliesCounts, sessionNet: null, available: true, note: null },
    {
      // earnings has no per-tick entry-attempts concept the way meic/flies/pmcc/curve/calendars/
      // bwb do -- it screens candidate SYMBOLS rather than evaluating entry ticks on an
      // already-chosen structure. earningsCounts (above) maps this card's columns onto earnings'
      // OWN vocabulary instead of forcing a fit onto the shared attempts-timeline shape: filled =
      // opened, refused = rejected (screened out), no fill = accepted but not yet opened.
      module: "earnings",
      ...earningsCounts,
      sessionNet: null,
      available: true,
      note: null,
    },
    { module: "calendars", ...calendarsCounts, sessionNet: null, available: true, note: null },
    { module: "pmcc", ...pmccCounts, sessionNet: null, available: true, note: null },
    { module: "curve", ...curveCounts, sessionNet: null, available: true, note: null },
    { module: "bwb", ...bwbCounts, sessionNet: null, available: true, note: null },
  ];

  const suite = buildSuiteReport(config);
  const lastSession = suite.daily.length > 0 ? suite.daily[suite.daily.length - 1] : undefined;
  for (const row of entries) {
    if (lastSession !== undefined && row.module in lastSession.byModule) {
      row.sessionNet = lastSession.byModule[row.module] ?? null;
    }
  }

  const breakByModule: Record<string, { date: string; note: string | null } | undefined> = {
    calendars: calendars.integrity.measurementBreaks[0]
      ? { date: calendars.integrity.measurementBreaks[0].date, note: calendars.integrity.measurementBreaks[0].note }
      : undefined,
    pmcc: pmcc.integrity.measurementBreaks[0]
      ? { date: pmcc.integrity.measurementBreaks[0].date, note: pmcc.integrity.measurementBreaks[0].note }
      : undefined,
    curve: curve.integrity.measurementBreaks[0]
      ? { date: curve.integrity.measurementBreaks[0].date, note: curve.integrity.measurementBreaks[0].note }
      : undefined,
    bwb: bwb.integrity.measurementBreaks[0]
      ? { date: bwb.integrity.measurementBreaks[0].date, note: bwb.integrity.measurementBreaks[0].note }
      : undefined,
  };

  const evidence: DeskEvidenceRow[] = Object.keys(suite.modules).map((mod) => {
    const b = breakByModule[mod];
    const lastBreakDate = b?.date ?? null;
    const lastBreakReason = b?.note ?? null;
    const sessionsSince =
      lastBreakDate !== null
        ? suite.daily.filter((d) => mod in d.byModule && d.session > lastBreakDate).length
        : null;
    return { module: mod, lastBreakDate, lastBreakReason, sessionsSince };
  });

  const lastSessionFacts = lastSession !== undefined ? readFactSet(config, lastSession.session) : null;
  const eodRows: DeskEodRow[] = Object.entries(lastSession?.byModule ?? {}).map(([mod, net]) => {
    const closed = lastSessionFacts?.[mod]?.closed ?? null;
    return { module: mod, net, closed, netPerTrade: closed !== null && closed > 0 ? net / closed : null };
  });

  return {
    mode: "paper",
    liveness: livenessRows,
    exposure,
    entries,
    evidence,
    eod: { session: lastSession?.session ?? null, rows: eodRows },
  };
}
