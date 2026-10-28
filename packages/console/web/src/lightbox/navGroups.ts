import type { ModuleId } from "./moduleOrder";
import { SECTIONS } from "../pages/Config/fieldMeta";

/**
 * What the nav rail knows about a module's tabs, WITHOUT loading that module's code.
 *
 * Every manifest is `lazy()`, and React's server renderer emits the Suspense fallback for an
 * unresolved lazy component rather than waiting for it — so anything that lives inside a manifest
 * is invisible to `renderToString`, which is the only kind of test this package has. The rail and
 * the breadcrumb are the two things a route test most needs to assert on, so the tab list has to
 * live out here as plain data. It is also what lets `j`/`k` step tabs without the module being
 * open yet.
 *
 * That makes this a second copy of something the manifest also states, which is the shape of
 * mistake this repo takes seriously. Two guards, rather than trust: the manifest builds its slides
 * through `fliesSlide()`, so the ids are typed against this file and a typo will not compile; and
 * `ModuleFrame` compares the slides it was handed against `navSlideIds()` at runtime and renders a
 * visible warning chip for any divergence. A drift that only shows as a missing rail entry would
 * read as "that tab was removed".
 */

export interface NavSlide {
  id: string;
  label: string;
}

export interface NavGroup {
  label: string;
  ids: string[];
}

export interface ModuleNavDecl {
  slides: readonly NavSlide[];
  groups?: readonly NavGroup[];
  /** Ids that used to name a tab, mapped to the tab that replaced them. */
  legacy?: Readonly<Record<string, string>>;
  /** The tabs the SIMPLE view shows, in the module's own vocabulary; the detail view shows every
   *  tab. Absent means the module has not chosen, and both views show everything. */
  simple?: readonly string[];
}

/**
 * Flies' tabs. Five were renamed when the module moved to the frame (2026-09-22):
 *
 * - `now` → `session`, because the header's date filter can select a past day and "now" then
 *   names the wrong thing; the card inside already said "latest session — {date}".
 * - `exits` → `divergence`: the slide renders the arm-divergence card and nothing else. Flies has
 *   no exit data — positions settle at expiry.
 * - `calibration` → `completion`: the slide is the module's own question (completion, roll, why
 *   misses missed, live vs paper). `performance` stays the name of the suite-wide calibration
 *   reading every module shares, which is what `calibration` sounded like it meant.
 * - `journal` → `decisions`, matching what bwb/pmcc/curve/calendars already call the same thing.
 * - `openrange` → `openingrange`, matching the card, the doc and the study.
 *
 * `books`, `positions` and `history` are the three dense tables. From 2026-09-22 they opened as
 * overlay sheets from the cards whose numbers they explain; since 2026-09-24 they are pages in the
 * rail's `tables` group, and those cards link to them -- nothing on the surface opens an overlay,
 * so every place a reader can go can be reloaded, shared and reached from the rail. The old
 * `trades` id (the tab before the sheets) reaches `positions`.
 *
 * 2026-10-12: `divergence` → `gates`. The arm-divergence card answered GEX-centre agreement, a
 * question retired with the `gex` arm on 2026-08-21; the tab is now the gate arms against control
 * over each arm's own window. `divergence` and `exits` both reach it. `livegap` (live vs paper,
 * paired per session) and `selector` joined the evidence group, and `openingrange` moved there:
 * it is a study of past sessions, not today's book.
 */
export const FLIES_SLIDES = [
  { id: "session", label: "session" },
  { id: "summary", label: "summary" },
  { id: "forest", label: "forest" },
  { id: "timeline", label: "timeline" },
  { id: "positions", label: "positions" },
  { id: "history", label: "history" },
  { id: "books", label: "books" },
  { id: "openingrange", label: "opening range" },
  { id: "attempts", label: "attempts" },
  { id: "decisions", label: "decisions" },
  { id: "gates", label: "gates vs control" },
  { id: "livegap", label: "live vs paper" },
  { id: "selector", label: "selector" },
  { id: "regime", label: "regime cuts" },
  { id: "agent", label: "intraday agent" },
  { id: "completion", label: "completion" },
  { id: "performance", label: "performance" },
  { id: "analysis", label: "analysis" },
  { id: "costs", label: "costs" },
  { id: "advisor", label: "advisor" },
  { id: "guide", label: "help" },
] as const satisfies readonly NavSlide[];

export type FliesSlideId = (typeof FLIES_SLIDES)[number]["id"];

/**
 * MEIC's tabs, on the frame since 2026-09-25. Three changes of meaning, all toward what flies
 * already calls the same things:
 *
 * - `now` → `session`: it shows the resolved session (the header's day select), not the calendar
 *   day. The calendar periods (today / week / month / all) moved to `sessions`, beside the daily
 *   summaries, since they are multi-session readings.
 * - `trades` → two tables in the suite's standard layout: `positions` (every trade the session
 *   held, entry side) and `history` (the closed ones: exit, how it ended, gross, fees, settlement,
 *   slippage, net). The old `trades` id reaches `history`.
 * - The old `history` (deep cards and daily summaries) is `sessions`. Its id now names the trade
 *   history, which is what the same id means on flies — a bookmark to it lands on the closest thing.
 */
export const MEIC_SLIDES = [
  { id: "session", label: "session" },
  // The gauge-led day summary (2026-10): the slide whose adopted visual language became the
  // suite's theme, and the pattern for every module's own summary — one per module, each in its
  // own KPI vocabulary; no central summary page.
  { id: "summary", label: "summary" },
  { id: "forest", label: "forest" },
  { id: "attempts", label: "attempts" },
  { id: "positions", label: "positions" },
  { id: "history", label: "history" },
  { id: "sessions", label: "sessions" },
  { id: "exits", label: "exits" },
  { id: "regime", label: "regime cuts" },
  { id: "calibration", label: "calibration" },
  { id: "performance", label: "performance" },
  { id: "analysis", label: "analysis" },
  { id: "costs", label: "costs" },
  { id: "advisor", label: "advisor" },
  { id: "guide", label: "help" },
] as const satisfies readonly NavSlide[];

export type MeicSlideId = (typeof MEIC_SLIDES)[number]["id"];

/**
 * bwb's tabs, on the frame since 2026-09-25. `now` → `session` (tiles and shapes, each linking
 * into the rail); its dense cards became pages: `positions` (open trades, marked), `arms` (net by
 * arm beside the fire counts, with the module's honesty caveats) and `decisions`. `history` is the
 * completed positions in the suite's standard trade layout.
 *
 * 2026-10-12: `vol` (the entry term structure and implied variance, `regime-split`, `iv-premium`)
 * and `livegap` (the live arm against paper's same arm, with its refusals named by gate) joined
 * the evidence group. Both are the module's own reads, bridged.
 */
export const BWB_SLIDES = [
  { id: "session", label: "session" },
  { id: "summary", label: "summary" },
  { id: "positions", label: "positions" },
  { id: "history", label: "history" },
  { id: "decisions", label: "decisions" },
  { id: "arms", label: "arms" },
  { id: "vol", label: "entry vol" },
  { id: "livegap", label: "live vs paper" },
  { id: "performance", label: "performance" },
  { id: "analysis", label: "analysis" },
  { id: "advisor", label: "advisor" },
  { id: "guide", label: "help" },
] as const satisfies readonly NavSlide[];

export type BwbSlideId = (typeof BWB_SLIDES)[number]["id"];

/**
 * earnings' tabs, on the frame since 2026-09-25. The old `now` (live marks and the management log)
 * and `overview` (KPIs, the strategy comparison, open positions) are split by what they are:
 * `session` is the book in tiles and shapes, `positions` the open trades (live marks, then the
 * entry side), `decisions` the management log, `strategies` the comparison beside the old
 * `detail`, and `screening` the entry reviews that sat under the trade log. `history` is the closed
 * trades in the suite's standard layout; the old `trades` id reaches it.
 *
 * 2026-10-23, eleven tabs to eight. `strategies` is the verdict board (the module's
 * `strategy_report --json`: net ÷ capital at risk with an event-clustered interval, where the money
 * goes, a drill-down per arm under `?arm=`), and it absorbed `performance` and `analysis`, which
 * answered the same question three ways that disagreed. `screening` absorbed `upcoming` and the
 * rejection funnel that sat on the strategies page: the forward scan, the screen's verdicts and
 * why it refused, in one place. The old ids reach their new homes. `summary` stays: it is the
 * module's simple-view page, a suite pattern (packages/console/docs/pages.md, "A summary slide is a module's own").
 */
export const EARNINGS_SLIDES = [
  { id: "session", label: "session" },
  { id: "summary", label: "summary" },
  { id: "positions", label: "positions" },
  { id: "history", label: "history" },
  { id: "decisions", label: "decisions" },
  { id: "screening", label: "screening" },
  { id: "strategies", label: "strategies" },
  { id: "advisor", label: "advisor" },
] as const satisfies readonly NavSlide[];

export type EarningsSlideId = (typeof EARNINGS_SLIDES)[number]["id"];

/**
 * curve's tabs, on the frame since 2026-09-25. `now` → `session`; its cards became pages: `regime`
 * (the daily VIX/VIX3M read, recorded every session traded or not), `decisions`, `arms` (net by
 * arm with the noflip divergence) and `positions`. `history` is the completed cycles in the
 * suite's standard trade layout.
 */
export const CURVE_SLIDES = [
  { id: "session", label: "session" },
  { id: "summary", label: "summary" },
  { id: "regime", label: "regime" },
  { id: "positions", label: "positions" },
  { id: "history", label: "history" },
  { id: "decisions", label: "decisions" },
  { id: "arms", label: "arms" },
  { id: "performance", label: "performance" },
  { id: "analysis", label: "analysis" },
  { id: "costs", label: "costs" },
  { id: "advisor", label: "advisor" },
  { id: "guide", label: "help" },
] as const satisfies readonly NavSlide[];

export type CurveSlideId = (typeof CURVE_SLIDES)[number]["id"];

/**
 * contango's tabs (2026-10-06): the VIX/VIX3M switch held in shares. `session` is today's read and
 * each arm's holding; `arms` and `performance` are the daily-NAV tear sheet against buy-and-hold
 * and against the arm's own rule; `costs` is every fill's slippage against the replay's 2 bps,
 * the missed windows and the distributions. No advisor tab: the module declares no advice bounds.
 */
export const CONTANGO_SLIDES = [
  { id: "session", label: "session" },
  { id: "summary", label: "summary" },
  { id: "regime", label: "regime" },
  { id: "positions", label: "positions" },
  { id: "history", label: "history" },
  { id: "decisions", label: "decisions" },
  { id: "arms", label: "arms" },
  { id: "performance", label: "performance" },
  { id: "analysis", label: "analysis" },
  { id: "costs", label: "costs" },
  { id: "guide", label: "help" },
] as const satisfies readonly NavSlide[];

export type ContangoSlideId = (typeof CONTANGO_SLIDES)[number]["id"];

/**
 * PMCC's tabs, on the frame since 2026-09-25. `now` → `session`; its cards became pages:
 * `decisions` (the arm rail, the attempt timeline, today's attempts and management verdicts, the
 * decision log), `arms` (the arm comparison) and `positions`. `history` is the completed cycles in
 * the suite's standard trade layout, each still expanding to its legs, rolls and settlement.
 */
export const PMCC_SLIDES = [
  { id: "session", label: "session" },
  { id: "summary", label: "summary" },
  { id: "positions", label: "positions" },
  { id: "tracker", label: "tracker" },
  { id: "history", label: "history" },
  { id: "decisions", label: "decisions" },
  { id: "arms", label: "arms" },
  { id: "performance", label: "performance" },
  { id: "analysis", label: "analysis" },
  { id: "costs", label: "costs" },
  { id: "advisor", label: "advisor" },
  { id: "guide", label: "help" },
] as const satisfies readonly NavSlide[];

export type PmccSlideId = (typeof PMCC_SLIDES)[number]["id"];

/**
 * calendars' tabs, on the frame since 2026-09-25. `now` → `session`; its cards became pages: `plan`
 * (the week's entry plan and window), `decisions` (the collapsed journal), `arms` and `positions`
 * (this week's structures and anything carried). `weeks` → `history`: every week per arm in the
 * suite's standard trade layout, each still expanding to its legs.
 */
export const CALENDARS_SLIDES = [
  { id: "session", label: "session" },
  { id: "summary", label: "summary" },
  { id: "plan", label: "plan" },
  { id: "positions", label: "positions" },
  { id: "history", label: "history" },
  { id: "decisions", label: "decisions" },
  { id: "arms", label: "arms" },
  { id: "policies", label: "policies" },
  { id: "performance", label: "performance" },
  { id: "analysis", label: "analysis" },
  { id: "advisor", label: "advisor" },
  { id: "guide", label: "help" },
] as const satisfies readonly NavSlide[];

export type CalendarsSlideId = (typeof CALENDARS_SLIDES)[number]["id"];

/**
 * The suite surfaces, on the frame since 2026-09-25 -- the last five, so the lightbox is gone.
 *
 * - GEX keeps its four former page tabs.
 * - Live: every live loop's day on one page, the manage evaluation's positions and portfolio, and a
 *   tab per live loop (hidden while its live switch is off) -- all read-only.
 * - Reports keeps its two artifacts, `morning` and `eod`, written by separate packages (the
 *   technicals chart moved to Charts).
 * - Advisor stays ONE page holding its own internal tabs (today / proposals / experiments /
 *   history). It carries the console's only two write actions besides Config's, wired through
 *   session and tab state that spans the page, and splitting that across frame pages is a change
 *   to a control path the suite keeps narrow -- a separate piece of work if it is ever wanted.
 * - Config has a page per `fieldMeta.ts` section plus `prefs`, read from that list rather than
 *   copied, so a section added there is a page here without a second edit.
 */
export const GEX_SLIDES = [
  { id: "gex", label: "gex" },
  { id: "skew", label: "iv skew" },
  { id: "volume", label: "volume" },
  { id: "history", label: "history" },
] as const satisfies readonly NavSlide[];

export type GexSlideId = (typeof GEX_SLIDES)[number]["id"];

// `today` (2026-10-15): every live loop on one page, first. Then the account-wide views from the
// manage evaluation (2026-10-18: `manage` split into `positions`, each trade's action, and
// `portfolio`, the account as one book), then each live loop's tab, named for its module (flies'
// was `today` until the summary took the name back the same day). `wheel` (2026-10-20) is the third
// account-wide view: each wheel cycle the evaluation follows; `pmcc` (the same day) the fourth, each
// PMCC on the real account -- not the paper pmcc module, which has its own page.
export const LIVE_SLIDES = [
  { id: "today", label: "today" },
  { id: "positions", label: "positions" },
  { id: "portfolio", label: "portfolio" },
  { id: "wheel", label: "wheel" },
  { id: "pmcc", label: "pmcc" },
  { id: "flies", label: "flies" },
  { id: "meic", label: "meic" },
  // B10 (2026-10-24): each live arm's fills and overrides by week, account-wide.
  { id: "execution", label: "execution" },
] as const satisfies readonly NavSlide[];

export const REPORTS_SLIDES = [
  { id: "morning", label: "morning" },
  { id: "eod", label: "eod" },
] as const satisfies readonly NavSlide[];

/**
 * Compare (2026-10-12): every arm beside its control, from `core.compare`. One tab; scope, sort and
 * the open arm are query parameters.
 */
export const COMPARE_SLIDES = [
  { id: "arms", label: "arms" },
  // 2026-10-17: every arm's risk shape over its own window, from core.analysis (docs/strategy-analysis-plan.md).
  { id: "risk", label: "risk" },
  // 2026-10-17: every arm's settled net per session as a month grid with weekly totals.
  { id: "calendar", label: "calendar" },
  // 2026-10-17: one chosen arm per module held together -- correlation, effective arms, drawdown benefit.
  { id: "portfolio", label: "portfolio" },
] as const satisfies readonly NavSlide[];

/**
 * Charts (2026-10-01): the live intraday futures chart and the technicals package's one-name chart.
 * `technicals` was Reports' `chart` tab; `/reports/chart` redirects here with its query string.
 */
export const CHARTS_SLIDES = [
  { id: "intraday", label: "intraday" },
  { id: "technicals", label: "technicals" },
  { id: "setups", label: "setups" },
] as const satisfies readonly NavSlide[];

/** The screeners (2026-10-18 order): our own screens first -- the earnings calendar and its record,
 *  the signal board, the skate screen (2026-10-19) -- then the income lists (one tab each,
 *  2026-10-16), all three our own since the vendor's subscription ended on 2026-10-24. The fidelity
 *  tab, how well our rules matched the vendor's, was retired on 2026-10-19 (`packages/technicals/CLAUDE.md`). */
export const SCREENERS_SLIDES = [
  { id: "earnings", label: "earnings" },
  { id: "earnings-record", label: "earnings record" },
  { id: "board", label: "signal board" },
  { id: "skate", label: "skate" },
  { id: "short-puts", label: "short puts" },
  { id: "covered-calls", label: "covered calls" },
  { id: "credit-spreads", label: "credit spreads" },
] as const satisfies readonly NavSlide[];

/**
 * Options flow (2026-10-03): QuikOptions' Hot Options Report, one capture a session
 * (`docs/quikoptions-plan.md`). `today` is the page at a glance and links into the four tables,
 * each the site's own table in full.
 */
export const FLOW_SLIDES = [
  { id: "today", label: "today" },
  { id: "derived", label: "derived flow" },
  { id: "birdseye", label: "birdseye" },
  { id: "trades", label: "trades" },
  { id: "spreads", label: "spreads" },
  { id: "voloi", label: "vol / OI" },
] as const satisfies readonly NavSlide[];

export type FlowSlideId = (typeof FLOW_SLIDES)[number]["id"];

export const ADVISOR_SLIDES = [{ id: "advisor", label: "advisor" }] as const satisfies readonly NavSlide[];

/**
 * Backtest (2026-10-17): `packages/backtest`'s replays over licensed vendor history. Shown only
 * while the suite says `backtest: true` (it fails closed, `visibility.ts`). `data` is the licence
 * clock and the stores, `validation` the vendor against the recorder, `runs` the replays, `queue`
 * the research tasks the supervisor runs (2026-10-24).
 */
export const BACKTEST_SLIDES = [
  { id: "data", label: "data" },
  { id: "validation", label: "validation" },
  { id: "runs", label: "runs" },
  { id: "queue", label: "queue" },
  { id: "slices", label: "slices" },
] as const satisfies readonly NavSlide[];

/**
 * System (2026-10-01): the suite's own health, read-only. `health` is the verdict at a glance; the
 * rest are the detail behind it, one subsystem a tab.
 */
export const SYSTEM_SLIDES = [
  { id: "health", label: "health" },
  { id: "supervisor", label: "supervisor" },
  { id: "modules", label: "modules" },
  { id: "data", label: "data" },
  { id: "environment", label: "environment" },
  { id: "logs", label: "logs" },
] as const satisfies readonly NavSlide[];

export const CONFIG_SLIDES: readonly NavSlide[] = [
  ...SECTIONS.map((s) => ({ id: s.id, label: s.title.toLowerCase() })),
  { id: "prefs", label: "prefs" },
];

/**
 * Every page in the suite declares its tabs here. A full `Record` since the last lightbox moved
 * (2026-09-25): a module added to `moduleOrder.ts` without a declaration does not compile, where
 * before it would have rendered an empty rail.
 */
export const NAV_DECL: Record<ModuleId, ModuleNavDecl> = {
  flies: {
    slides: FLIES_SLIDES,
    simple: ["summary", "session", "forest", "performance", "positions", "guide"],
    groups: [
      { label: "today", ids: ["session", "summary", "forest", "timeline"] },
      { label: "tables", ids: ["positions", "history", "books"] },
      {
        label: "evidence",
        ids: ["attempts", "decisions", "gates", "livegap", "selector", "openingrange", "regime", "agent"],
      },
      { label: "study", ids: ["completion", "performance", "analysis", "costs", "advisor"] },
      { label: "help", ids: ["guide"] },
    ],
    legacy: {
      now: "session",
      exits: "gates",
      divergence: "gates",
      calibration: "completion",
      journal: "decisions",
      openrange: "openingrange",
      trades: "positions",
    },
  },
  meic: {
    slides: MEIC_SLIDES,
    simple: ["summary", "forest", "performance", "positions", "guide"],
    groups: [
      { label: "today", ids: ["session", "summary", "forest", "attempts"] },
      { label: "tables", ids: ["positions", "history", "sessions"] },
      { label: "evidence", ids: ["exits", "regime"] },
      { label: "study", ids: ["calibration", "performance", "analysis", "costs", "advisor"] },
      { label: "help", ids: ["guide"] },
    ],
    legacy: { now: "session", trades: "history" },
  },
  bwb: {
    slides: BWB_SLIDES,
    simple: ["summary", "session", "performance", "positions", "guide"],
    groups: [
      { label: "today", ids: ["session", "summary"] },
      { label: "tables", ids: ["positions", "history"] },
      { label: "evidence", ids: ["decisions", "arms", "vol", "livegap"] },
      { label: "study", ids: ["performance", "analysis", "advisor"] },
      { label: "help", ids: ["guide"] },
    ],
    legacy: { now: "session" },
  },
  earnings: {
    slides: EARNINGS_SLIDES,
    simple: ["summary", "session", "strategies", "positions"],
    groups: [
      { label: "today", ids: ["session", "summary"] },
      { label: "tables", ids: ["positions", "history"] },
      { label: "evidence", ids: ["decisions", "screening"] },
      { label: "study", ids: ["strategies", "advisor"] },
    ],
    legacy: {
      now: "session",
      overview: "session",
      detail: "strategies",
      trades: "history",
      performance: "strategies",
      analysis: "strategies",
      upcoming: "screening",
    },
  },
  curve: {
    slides: CURVE_SLIDES,
    simple: ["summary", "session", "regime", "performance", "positions", "guide"],
    groups: [
      { label: "today", ids: ["session", "summary", "regime"] },
      { label: "tables", ids: ["positions", "history"] },
      { label: "evidence", ids: ["decisions", "arms"] },
      { label: "study", ids: ["performance", "analysis", "costs", "advisor"] },
      { label: "help", ids: ["guide"] },
    ],
    legacy: { now: "session" },
  },
  contango: {
    slides: CONTANGO_SLIDES,
    simple: ["summary", "session", "regime", "performance", "positions", "guide"],
    groups: [
      { label: "today", ids: ["session", "summary", "regime"] },
      { label: "tables", ids: ["positions", "history"] },
      { label: "evidence", ids: ["decisions", "arms"] },
      { label: "study", ids: ["performance", "analysis", "costs"] },
      { label: "help", ids: ["guide"] },
    ],
    legacy: {},
  },
  calendars: {
    slides: CALENDARS_SLIDES,
    simple: ["summary", "session", "plan", "performance", "positions", "guide"],
    groups: [
      { label: "today", ids: ["session", "summary", "plan"] },
      { label: "tables", ids: ["positions", "history"] },
      { label: "evidence", ids: ["decisions", "arms", "policies"] },
      { label: "study", ids: ["performance", "analysis", "advisor"] },
      { label: "help", ids: ["guide"] },
    ],
    legacy: { now: "session", weeks: "history" },
  },
  pmcc: {
    slides: PMCC_SLIDES,
    simple: ["summary", "session", "performance", "positions", "guide"],
    groups: [
      { label: "today", ids: ["session", "summary"] },
      { label: "tables", ids: ["positions", "tracker", "history"] },
      { label: "evidence", ids: ["decisions", "arms"] },
      { label: "study", ids: ["performance", "analysis", "costs", "advisor"] },
      { label: "help", ids: ["guide"] },
    ],
    legacy: { now: "session" },
  },
  gex: { slides: GEX_SLIDES },
  flow: {
    slides: FLOW_SLIDES,
    simple: ["today"],
    groups: [
      { label: "today", ids: ["today", "derived"] },
      { label: "tables", ids: ["birdseye", "trades", "spreads", "voloi"] },
    ],
  },
  // Simple leaves out flies (2026-10-21): the module is retired, and its live tab is its history.
  live: { slides: LIVE_SLIDES, simple: ["today", "positions", "portfolio", "wheel", "pmcc", "meic"], legacy: { manage: "positions" } },
  charts: { slides: CHARTS_SLIDES },
  screeners: {
    slides: SCREENERS_SLIDES,
    simple: ["earnings", "earnings-record", "board", "skate", "short-puts", "covered-calls", "credit-spreads"],
    groups: [
      { label: "screens", ids: ["earnings", "earnings-record", "board", "skate"] },
      // Named for what the lists hold, not their source: they were the vendor's until 2026-10-24 and
      // are our own since, under the same names.
      { label: "income lists", ids: ["short-puts", "covered-calls", "credit-spreads"] },
    ],
  },
  reports: { slides: REPORTS_SLIDES },
  compare: { slides: COMPARE_SLIDES },
  backtest: { slides: BACKTEST_SLIDES },
  advisor: { slides: ADVISOR_SLIDES },
  system: { slides: SYSTEM_SLIDES },
  config: { slides: CONFIG_SLIDES },
};

/** The declared ids, in rail order. */
export function navSlideIds(module: ModuleId): string[] {
  return (NAV_DECL[module]?.slides ?? []).map((s) => s.id);
}

/**
 * Which tab a URL segment means.
 *
 * The order matters: a renamed tab's old id must reach its replacement, not fall through to the
 * module's first tab. `/flies/exits` landing on `session` would look like a working link and
 * silently show the wrong page, which is worse than a 404.
 */
export function resolveSlide(
  module: ModuleId,
  slide: string,
  slides: readonly { id: string }[],
  preferred: readonly { id: string }[] = slides,
): string {
  if (slides.some((s) => s.id === slide)) return slide;
  const alias = NAV_DECL[module]?.legacy?.[slide];
  if (alias !== undefined && slides.some((s) => s.id === alias)) return alias;
  // No tab named (or an unknown one): the view's first tab, so the simple view lands on its own.
  return preferred[0]?.id ?? slides[0]?.id ?? slide;
}

export type ViewMode = "simple" | "detail";

/**
 * The tabs a view lists. Detail is every tab; simple is the module's own `simple` list, in the
 * module's tab order. A tab the reader is ON stays listed even when the view leaves it out, so a
 * deep link into a detail tab is never a page the rail cannot place.
 */
export function viewSlides<T extends { id: string }>(
  module: ModuleId,
  slides: readonly T[],
  view: ViewMode,
  current: string | null = null,
): T[] {
  const simple = NAV_DECL[module]?.simple;
  if (view === "detail" || simple === undefined) return [...slides];
  return slides.filter((s) => simple.includes(s.id) || s.id === current);
}

export interface NavGrouping {
  groups: Array<{ label: string | null; slides: NavSlide[] }>;
  /** Declared slides that no group claims — they would vanish from the rail. */
  ungrouped: string[];
  /** Ids a group names that are not declared slides — they would render as nothing. */
  unknown: string[];
}

/**
 * The rail's sections. A module with no declared groups gets one unlabelled group holding every
 * slide in order, which is what an unswept module wants.
 *
 * `ungrouped` and `unknown` are returned rather than silently dropped because both failures are
 * invisible on the page: a slide in no group simply does not appear, and a group naming a slide
 * that does not exist renders one fewer link. `navGroups.test.ts` asserts both are empty.
 */
export function navGroups(module: ModuleId, slides: readonly NavSlide[]): NavGrouping {
  const decl = NAV_DECL[module];
  const byId = new Map(slides.map((s) => [s.id, s]));
  if (decl?.groups === undefined) {
    return { groups: [{ label: null, slides: [...slides] }], ungrouped: [], unknown: [] };
  }
  const unknown: string[] = [];
  const claimed = new Set<string>();
  const groups = decl.groups.map((g) => {
    const members: NavSlide[] = [];
    for (const id of g.ids) {
      const slide = byId.get(id);
      if (slide === undefined) {
        unknown.push(id);
        continue;
      }
      claimed.add(id);
      members.push(slide);
    }
    return { label: g.label, slides: members };
  });
  const ungrouped = slides.filter((s) => !claimed.has(s.id)).map((s) => s.id);
  return { groups, ungrouped, unknown };
}
