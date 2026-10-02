import path from "node:path";
import type { DeskBookPayload, DeskPayload, DeskLiveness, DeskExposureRow, DeskEntriesRow, DeskEvidenceRow, DeskEodRow, SuiteFeatures } from "@console/shared";
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
import { buildSuiteReport, readFactSet, readModuleBreaks } from "../services/report.js";
import { readModuleGate, sessionDateEt } from "../services/liveLock.js";
import { readScreenMetrics } from "../services/screenBridge.js";
import { moduleOn } from "../services/featuresBridge.js";

/**
 * Drop every row for a module the suite has turned off (`configcli` `features`, decided in Python).
 * The streamer is not a module and always stays. `features` undefined or a failed read keeps every
 * row: unknown is visible.
 */
function onlyEnabled<T>(rows: T[], idOf: (r: T) => string, features: SuiteFeatures | undefined): T[] {
  return rows.filter((r) => {
    const id = idOf(r);
    return id === "streamer" || moduleOn(features, id);
  });
}

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

/**
 * A per-share amount (a debit paid, a max loss) as the dollars it puts at risk: x100 x quantity.
 *
 * The ledgers store these per share, and this column summed them raw until 2026-09-24 -- so the
 * calendars, pmcc, curve and bwb rows read about 100x too small beside meic's, flies' and earnings'
 * true dollars under the one "at risk" header. Same rule as `core.ledgers`' `capital` for these four
 * modules: per-share x 100 x quantity, a missing quantity read as one contract.
 */
export function sumDollarsAtRisk<T extends { quantity?: number | null }>(
  positions: readonly T[],
  perShare: (p: T) => number | null | undefined,
): number | null {
  return positions.reduce<number | null>((s, p) => {
    const v = perShare(p);
    return v != null ? (s ?? 0) + v * 100 * (p.quantity ?? 1) : s;
  }, null);
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

export function readDesk(config: ConsoleConfig, features?: SuiteFeatures): DeskPayload {
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
      atRisk: sumDollarsAtRisk(calendars.openPositions, (p) => p.entryDebit),
      atRiskLabel: "debit at risk",
      unrealisedNet: sumField(calendars.openPositions as unknown as Record<string, unknown>[], "unrealisedNet"),
      markAgeSeconds: calendars.today.lastIteration?.ageSeconds ?? null,
      available: true,
      note: null,
    },
    {
      module: "pmcc",
      open: pmcc.openCount,
      atRisk: sumDollarsAtRisk(pmcc.openPositions, (p) => p.netDebit),
      atRiskLabel: "debit at risk",
      unrealisedNet: sumUnrealised(pmcc.openPositions),
      markAgeSeconds: pmcc.today.lastIteration?.ageSeconds ?? null,
      available: true,
      note: null,
    },
    {
      module: "curve",
      open: curve.openCount,
      atRisk: sumDollarsAtRisk(curve.openPositions, (p) => p.entryMaxLoss),
      atRiskLabel: "at risk",
      unrealisedNet: sumUnrealised(curve.openPositions),
      markAgeSeconds: curve.today.lastIteration?.ageSeconds ?? null,
      available: true,
      note: null,
    },
    {
      module: "bwb",
      open: bwb.openCount,
      atRisk: sumDollarsAtRisk(bwb.openPositions, (p) => p.entryMaxLoss),
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

  // Every module's journaled breaks (both ledger shapes, up to today). This used to read only calendars/pmcc/curve/bwb, so meic, flies and earnings
  // showed "no break" while their ledgers recorded several. The clock restarts on a WHOLE-BOOK break
  // only: an arm-scoped one (an arm added) changes that arm's evidence, not the others'.
  const breakByModule: Record<string, { date: string; note: string | null; armScoped: number } | undefined> = {};
  // Unbounded below: a module's last break can predate the era the curve starts at, and "no break"
  // would then be false.
  for (const [mod, rows] of Object.entries(readModuleBreaks(config, Object.keys(suite.modules), null))) {
    const whole = rows.find((r) => r.scope === null || r.scope === "*");
    const armScoped = rows.filter((r) => r.scope !== null && r.scope !== "*").length;
    breakByModule[mod] = whole
      ? { date: whole.date, note: whole.note ?? whole.key, armScoped }
      : armScoped > 0
        ? { date: "", note: null, armScoped }
        : undefined;
  }

  const evidence: DeskEvidenceRow[] = Object.keys(suite.modules).map((mod) => {
    const b = breakByModule[mod];
    const lastBreakDate = b?.date ? b.date : null;
    const scoped = b && b.armScoped > 0 ? ` (+${b.armScoped} arm-scoped break${b.armScoped === 1 ? "" : "s"} since the era began)` : "";
    const lastBreakReason = b ? `${b.note ?? ""}${scoped}` || null : null;
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

  const keep = <T,>(rows: T[], idOf: (r: T) => string): T[] => onlyEnabled(rows, idOf, features);
  return {
    mode: "paper",
    liveness: keep(livenessRows, (r) => r.id),
    exposure: keep(exposure, (r) => r.module),
    entries: keep(entries, (r) => r.module),
    evidence: keep(evidence, (r) => r.module),
    eod: { session: lastSession?.session ?? null, rows: keep(eodRows, (r) => r.module) },
  };
}

/**
 * The live flies loop journals DECISIONS (`fly_decisions`: reason, accepted, occurrences), not the
 * per-tick `fly_entry_attempts` the paper loop writes -- that table is empty in the live ledger, so
 * the shared attempts read counted nothing beside a session net of six trades. Filled and no-fill
 * come from the positions the session entered (a cancelled entry is one that never filled);
 * refused is the decisions it turned down, by occurrence, the same measure as paper's refusals.
 */
export function readFliesLiveEntries(config: ConsoleConfig, session: string | null): { filled: number; refused: number; noFill: number; topRefusal: string | null } {
  const empty = { filled: 0, refused: 0, noFill: 0, topRefusal: null };
  if (session === null) return empty;
  return withReadOnlyDb(path.join(config.paths.fliesDir, "live_trades.db"), empty, (db) => {
    const pos = db
      .prepare("SELECT status, COUNT(*) AS n FROM fly_positions WHERE trade_date = ? GROUP BY status")
      .all(session) as Array<{ status: string | null; n: number }>;
    const refusals = db
      .prepare(
        "SELECT reason, SUM(occurrences) AS n FROM fly_decisions WHERE trade_date = ? AND accepted = 0 GROUP BY reason ORDER BY n DESC",
      )
      .all(session) as Array<{ reason: string; n: number }>;
    const top = refusals[0];
    return {
      filled: pos.filter((r) => r.status !== "cancelled" && r.status !== "voided").reduce((t, r) => t + r.n, 0),
      noFill: pos.filter((r) => r.status === "cancelled").reduce((t, r) => t + r.n, 0),
      refused: refusals.reduce((t, r) => t + r.n, 0),
      topRefusal: top !== undefined ? `${top.reason} ×${String(top.n)}` : null,
    };
  });
}

/** Calendars, pmcc and curve are paper-only by design: they have no live book to show. */
const PAPER_ONLY = ["calendars", "pmcc", "curve"] as const;
const PAPER_ONLY_NOTE = "paper-only · no live path";
const LIVE_OFF_NOTE = "live trading off";

/**
 * The live book's exposure and entries, for the Overview's cards to rotate to. The same readers as
 * the paper composition above, asked for `live`: meic, flies, earnings and bwb each have a narrow
 * live path; the paper-only modules are rows that say so rather than zeros.
 *
 * Session net comes from the live ledger only where a reader states one (flies' latest live
 * session). The paper column reads the suite report, which is paper by construction, so the other
 * live rows show no figure rather than borrow one.
 */
export function readDeskLive(config: ConsoleConfig, features?: SuiteFeatures): DeskBookPayload {
  const fliesAnalytics = readFliesAnalytics(config, "live", { arm: null, date: null, symbol: null, era: null });
  const meicExposure = readMeicOpenExposure(config, "live");
  const earningsDetail = readEarningsDetail(config, "live", null);
  const bwb = readBwb(config, "live");
  const meicLoop = readMeicLoopStatus(config, "live");
  const fliesLoop = readFliesLoopStatus(config, "live");

  const paperOnlyExposure = (module: string): DeskExposureRow => ({
    module, open: null, atRisk: null, atRiskLabel: "", unrealisedNet: null, markAgeSeconds: null, available: false, note: PAPER_ONLY_NOTE,
  });
  const exposure: DeskExposureRow[] = [
    {
      module: "meic",
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
      markAgeSeconds: null,
      available: true,
      note: null,
    },
    ...PAPER_ONLY.map(paperOnlyExposure),
    {
      module: "bwb",
      open: bwb.openCount,
      atRisk: sumDollarsAtRisk(bwb.openPositions, (p) => p.entryMaxLoss),
      atRiskLabel: "at risk (zero-floor by design)",
      unrealisedNet: sumUnrealised(bwb.openPositions),
      markAgeSeconds: bwb.today.lastIteration?.ageSeconds ?? null,
      available: true,
      note: null,
    },
  ];

  const meicCounts = countOutcomes(readEntryAttempts(config, "meic", "live", null).timeline);
  const fliesCounts = readFliesLiveEntries(config, fliesAnalytics.today.tradeDate);
  const bwbCounts = countOutcomes(
    bwb.entryAttemptsToday.map((a: { outcome: string }) => ({ outcome: a.outcome, blockDetail: null })),
  );
  const earningsMetrics = readScreenMetrics("live", sessionDateEt()).metrics;
  const funnel = earningsMetrics?.funnel ?? null;
  const earningsCounts = {
    filled: funnel?.opened ?? 0,
    refused: funnel?.rejected ?? 0,
    noFill: funnel !== null ? Math.max(0, funnel.accepted - funnel.opened) : 0,
    topRefusal: earningsMetrics?.reasons[0]?.reason ?? null,
  };
  const paperOnlyEntries = (module: string): DeskEntriesRow => ({
    module, filled: 0, refused: 0, noFill: 0, sessionNet: null, topRefusal: null, available: false, note: PAPER_ONLY_NOTE,
  });
  const entries: DeskEntriesRow[] = [
    { module: "meic", ...meicCounts, sessionNet: null, available: true, note: null },
    {
      module: "flies",
      ...fliesCounts,
      sessionNet: fliesAnalytics.today.tradeDate !== null ? fliesAnalytics.today.netPnl : null,
      available: true,
      note: null,
    },
    { module: "earnings", ...earningsCounts, sessionNet: null, available: true, note: null },
    ...PAPER_ONLY.map(paperOnlyEntries),
    { module: "bwb", ...bwbCounts, sessionNet: null, available: true, note: null },
  ];
  // A module whose live gate is off has no live loop to report on: its last mark is however long
  // ago the gate was shut (meic's was 84 days on 2026-10-01), which reads as a stalled loop. It says
  // "live trading off" instead -- unless its live book still holds something, which stays visible.
  const off = new Set(
    exposure
      .filter((r) => r.available && readModuleGate(config, r.module).liveEnabled !== true)
      .filter((r) => (r.open ?? 0) === 0)
      .map((r) => r.module),
  );
  const offExposure = exposure.map((r): DeskExposureRow =>
    off.has(r.module)
      ? { ...r, open: null, atRisk: null, unrealisedNet: null, markAgeSeconds: null, available: false, note: LIVE_OFF_NOTE }
      : r,
  );
  const offEntries = entries.map((r): DeskEntriesRow =>
    off.has(r.module) && r.filled + r.refused + r.noFill === 0 ? { ...r, available: false, note: LIVE_OFF_NOTE } : r,
  );
  return {
    mode: "live",
    exposure: onlyEnabled(offExposure, (r) => r.module, features),
    entries: onlyEnabled(offEntries, (r) => r.module, features),
  };
}
