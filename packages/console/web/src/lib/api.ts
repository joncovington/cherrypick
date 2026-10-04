import { useQuery } from "@tanstack/react-query";
import { normalizeAdvisorPayload } from "./advisorShape";
import type {
  OverviewPayload,
  StatusPayload,
  MeicPayload,
  FliesPayload,
  EarningsPayload,
  GexPayload,
  Paged,
  TradingMode,
  TtWatchlistIndex,
  TtWatchlistPayload,
  TtWatchlistRow,
  ReviewPayload,
  AdvisorPayload,
  MorningPayload,
  OptionsFlowPayload,
  TechnicalsChartPayload,
  TechnicalsWatchlist,
  PmccPayload,
  PmccCycleRow,
  PmccHistory,
  PmccMeta,
  PmccAssignment,
  PmccBridged,
  PmccTracker,
  PmccTrackerIndexRow,
  PmccWeeklyRow,
  CurvePayload,
  CurveCycleRow,
  CurveHistory,
  CurveMeta,
  BwbPayload,
  BwbCycleRow,
  BwbHistory,
  BwbMeta,
  CalendarsPayload,
  CalendarsPoliciesPayload,
  CalendarsPosition,
  CalendarsWeekRow,
  CalendarsWeeks,
  DeskBookPayload,
  DeskPayload,
  FuturesTickerPayload,
  SystemDataPayload,
  SystemEnvironmentPayload,
  SystemHealthPayload,
  SystemLogSource,
  SystemModulesPayload,
  SystemSupervisorPayload,
} from "@console/shared";

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

let csrfToken: string | null = null;

export async function getCsrf(): Promise<string> {
  if (csrfToken !== null) return csrfToken;
  const { token } = await getJson<{ token: string }>("/api/csrf");
  csrfToken = token;
  return token;
}

export async function mutateJson<T>(url: string, method: "POST" | "DELETE", body?: unknown): Promise<T> {
  const token = await getCsrf();
  const res = await fetch(url, {
    method,
    headers: {
      "x-csrf-token": token,
      ...(body !== undefined ? { "content-type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

export function useStatus() {
  return useQuery<StatusPayload>({
    queryKey: ["status"],
    queryFn: () => getJson<StatusPayload>("/api/status"),
    refetchInterval: 5_000,
  });
}

export function useOverview() {
  return useQuery<OverviewPayload>({
    queryKey: ["overview"],
    queryFn: () => getJson<OverviewPayload>("/api/overview"),
    refetchInterval: 15_000,
  });
}

/** The Overview's suite matrix (liveness, exposure, entries, evidence clock, EOD) -- same cadence
 *  as the per-module dashboards, since it composes their own readers. */
export function useDesk() {
  return useQuery<DeskPayload>({
    queryKey: ["desk"],
    queryFn: () => getJson<DeskPayload>("/api/desk"),
    refetchInterval: 15_000,
  });
}

/** The live book's exposure and entries, which the Overview's cards rotate to. */
export function useDeskLive() {
  return useQuery<DeskBookPayload>({
    queryKey: ["desk", "live"],
    queryFn: () => getJson<DeskBookPayload>("/api/desk/live"),
    refetchInterval: 15_000,
  });
}

export function useReview(session?: string) {
  return useQuery<ReviewPayload>({
    queryKey: ["review", session ?? "latest"],
    queryFn: () => getJson<ReviewPayload>(`/api/review${session ? `?session=${session}` : ""}`),
    // The fact set changes twice a day, not continuously — polling it hard would be noise.
    refetchInterval: 60_000,
  });
}

/** Which contract each futures-ticker product is today. The map changes once a day at most; the
 *  prices are on the quote socket, so this only needs to notice a roll. */
export function useFuturesTicker() {
  return useQuery<FuturesTickerPayload>({
    queryKey: ["futures-ticker"],
    queryFn: () => getJson<FuturesTickerPayload>("/api/futures-ticker"),
    refetchInterval: 300_000,
  });
}

/** The System page, one query per tab at the cadence its data changes. */
export function useSystemHealth() {
  return useQuery<SystemHealthPayload>({
    queryKey: ["system", "health"],
    queryFn: () => getJson<SystemHealthPayload>("/api/system/health"),
    refetchInterval: 10_000,
  });
}

export function useSystemSupervisor() {
  return useQuery<SystemSupervisorPayload>({
    queryKey: ["system", "supervisor"],
    queryFn: () => getJson<SystemSupervisorPayload>("/api/system/supervisor"),
    refetchInterval: 5_000,
  });
}

export function useSystemModules() {
  return useQuery<SystemModulesPayload>({
    queryKey: ["system", "modules"],
    queryFn: () => getJson<SystemModulesPayload>("/api/system/modules"),
    refetchInterval: 15_000,
  });
}

export function useSystemData() {
  return useQuery<SystemDataPayload>({
    queryKey: ["system", "data"],
    queryFn: () => getJson<SystemDataPayload>("/api/system/data"),
    refetchInterval: 30_000,
  });
}

export function useSystemEnvironment() {
  return useQuery<SystemEnvironmentPayload>({
    queryKey: ["system", "environment"],
    queryFn: () => getJson<SystemEnvironmentPayload>("/api/system/environment"),
    refetchInterval: 60_000,
  });
}

export function useLogSources() {
  return useQuery<{ sources: SystemLogSource[] }>({
    queryKey: ["system", "log-sources"],
    queryFn: () => getJson<{ sources: SystemLogSource[] }>("/api/system/log-sources"),
    refetchInterval: 300_000,
  });
}

export interface LogLine {
  source: string;
  level: string;
  ts: string | null;
  text: string;
}

/** One log, or (no source) the merged view of watchdog, notify and the trading loops. */
export function useLogs(source: string | null, limit: number) {
  const qs = new URLSearchParams({ limit: String(limit), ...(source !== null ? { source } : {}) });
  return useQuery<{ lines: LogLine[] }>({
    queryKey: ["logs", source ?? "merged", limit],
    queryFn: () => getJson<{ lines: LogLine[] }>(`/api/logs?${qs.toString()}`),
    refetchInterval: 10_000,
  });
}

export function useMorningReport(session?: string) {
  return useQuery<MorningPayload>({
    queryKey: ["morning", session ?? "latest"],
    queryFn: () => getJson<MorningPayload>(`/api/morning${session ? `?session=${session}` : ""}`),
    // The pack is written once before the open (the narrative may land a little later) — a minute
    // is already far finer than the data.
    refetchInterval: 60_000,
  });
}

/** QuikOptions' Hot Options Report for a session (latest when none): one capture a day, after the
 *  close, so a few minutes is already far finer than the data. */
export function useOptionsFlow(session?: string) {
  return useQuery<OptionsFlowPayload>({
    queryKey: ["options-flow", session ?? "latest"],
    queryFn: () =>
      getJson<OptionsFlowPayload>(`/api/options-flow${session ? `?session=${encodeURIComponent(session)}` : ""}`),
    refetchInterval: 300_000,
  });
}

export function useTechnicalsChart(symbol?: string) {
  return useQuery<TechnicalsChartPayload>({
    queryKey: ["technicals-chart", symbol ?? ""],
    queryFn: () =>
      getJson<TechnicalsChartPayload>(
        `/api/technicals/chart${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`,
      ),
    // Written once a session by the report job.
    refetchInterval: 300_000,
  });
}

export function useSetupsWatchlist() {
  return useQuery<TechnicalsWatchlist>({
    queryKey: ["technicals-setups"],
    queryFn: () => getJson<TechnicalsWatchlist>("/api/technicals/setups"),
    // Written once a session by the report job, with the chart files.
    refetchInterval: 300_000,
  });
}

export function useAdvisor(session?: string) {
  return useQuery<AdvisorPayload>({
    queryKey: ["advisor", session ?? "latest"],
    // Normalised at the boundary: a freshly built page can meet the previous build's API until
    // the supervisor restarts the server (`lib/advisorShape.ts`).
    queryFn: async () => normalizeAdvisorPayload(await getJson<unknown>(`/api/advisor${session ? `?session=${session}` : ""}`)),
    // Four checkpoints a day and one nightly enact — a minute is already far finer than the data.
    refetchInterval: 60_000,
  });
}

// Both actions carry an empty JSON body they have no use for: the mutating-surface guard in
// security.ts requires `content-type: application/json` on every POST, and mutateJson only sets
// that header when there is a body to send.
export async function killAdvisorExperiment(id: string): Promise<AdvisorPayload> {
  return mutateJson<AdvisorPayload>(`/api/advisor/experiments/${encodeURIComponent(id)}/kill`, "POST", {});
}

export async function dismissAdvisorProposal(id: number): Promise<AdvisorPayload> {
  return mutateJson<AdvisorPayload>(`/api/advisor/proposals/${String(id)}/dismiss`, "POST", {});
}

export interface MeicTradeQuery extends DateRangeQuery {
  /** null = the latest session, resolved server-side like every other Today card. */
  day: string | null;
  symbol: string | null;
  profile: string | null;
  era: string | null;
  outcome: string;
  /** `positions`: every trade the session held; `history`: the closed ones. */
  view: "positions" | "history";
  reason: string | null;
  search: string;
  limit: number;
  offset: number;
}

export function useMeic(mode: TradingMode, q: MeicTradeQuery) {
  const params = new URLSearchParams({
    mode,
    outcome: q.outcome,
    view: q.view,
    search: q.search,
    limit: String(q.limit),
    offset: String(q.offset),
  });
  if (q.day !== null) params.set("date", q.day);
  if (q.symbol !== null) params.set("symbol", q.symbol);
  if (q.profile !== null) params.set("profile", q.profile);
  if (q.era !== null) params.set("era", q.era);
  if (q.reason !== null) params.set("reason", q.reason);
  rangeParams(params, q);
  return useQuery<MeicPayload>({
    queryKey: ["meic", mode, q.view, q.day, q.from, q.to, q.symbol, q.profile, q.era, q.outcome, q.reason, q.search, q.limit, q.offset],
    queryFn: () => getJson<MeicPayload>(`/api/meic?${params.toString()}`),
    refetchInterval: 15_000,
    // A page that briefly empties while the next one loads reads as "no
    // trades"; holding the previous page keeps paging visually continuous.
    placeholderData: (prev) => prev,
  });
}

export interface FliesFilter {
  arm: string | null;
  date: string | null;
  /** null = every symbol in scope. Only meaningful with era "ALL" — the current era is SPX alone. */
  symbol: string | null;
  /** null = the module's current era (SPX from 2026-08-01); "ALL" = every era, a stated choice. */
  era: string | null;
}

export function fliesQuery(mode: TradingMode, filter: FliesFilter): string {
  const params = new URLSearchParams({ mode });
  if (filter.arm !== null) params.set("arm", filter.arm);
  if (filter.date !== null) params.set("date", filter.date);
  if (filter.symbol !== null) params.set("symbol", filter.symbol);
  if (filter.era !== null) params.set("era", filter.era);
  return params.toString();
}

/**
 * A history table's date range, both sides inclusive and either open (components/table/DateRange).
 * Optional on the filters that carry it, so a caller with no range control need not name one.
 */
export interface DateRangeQuery {
  from?: string | null;
  to?: string | null;
}

export const NO_DATE_RANGE: DateRangeQuery = {};

function rangeParams(params: URLSearchParams, r: DateRangeQuery): void {
  if (r.from != null) params.set("from", r.from);
  if (r.to != null) params.set("to", r.to);
}

export interface PageState {
  limit: number;
  offset: number;
}

export const FIRST_PAGE: PageState = { limit: 100, offset: 0 };
/** Mirrors the server's PAGE_SIZES; anything larger is clamped there. */
export const PAGE_SIZES = [50, 100, 200, 500] as const;

/** Serialize one table's page under its own prefix, so several can share an endpoint. */
function pageParams(params: URLSearchParams, prefix: string, page: PageState): void {
  const key = (k: string): string => (prefix === "" ? k : `${prefix}${k[0]!.toUpperCase()}${k.slice(1)}`);
  params.set(key("limit"), String(page.limit));
  params.set(key("offset"), String(page.offset));
}

export function useFlies(mode: TradingMode, filter: FliesFilter, books: PageState, positions: PageState) {
  const params = new URLSearchParams(fliesQuery(mode, filter));
  pageParams(params, "books", books);
  pageParams(params, "positions", positions);
  return useQuery<FliesPayload>({
    queryKey: ["flies", mode, filter, books, positions],
    queryFn: () => getJson<FliesPayload>(`/api/flies?${params.toString()}`),
    refetchInterval: 15_000,
    placeholderData: (prev) => prev,
  });
}

export interface FliesTradeLogRow {
  tradeDate: string;
  /** Full ISO entry timestamp with its market offset — rendered from the string, never via a Date. */
  entryTime: string | null;
  symbol: string;
  arm: string | null;
  entryMode: string | null;
  kind: string | null;
  side: string | null;
  center: number | null;
  /** Near wing, in points. */
  wingWidth: number | null;
  /** Far wing, only when the wing is BROKEN; null on a symmetric fly. */
  farWidth: number | null;
  window: string | null;
  quantity: number | null;
  /** Net entry price per share, signed: credit +, debit −. */
  price: number | null;
  /** Whole-position dollars, signed cash flow: entry + exit = gross; gross − fees − settlement = net. */
  entryCash: number | null;
  exitCash: number | null;
  exitKind: "closed" | "settled" | "expired" | null;
  gross: number | null;
  /** Trading fees — the fee TOTAL when `settlementFees` is null (the split was never recorded). */
  fees: number | null;
  settlementFees: number | null;
  /** Already inside gross — informational, never subtracted. */
  slippage: number | null;
  pnl: number | null;
  latencyMin: number | null;
  pinned: boolean;
}

/** Scope-wide totals for the log — every matching row, never the rendered page. */
export interface FliesTradeLogTotals {
  trades: number;
  sessions: number;
  netPnl: number;
  grossPnl: number;
  fees: number;
  settlementFees: number;
  slippage: number;
  /** How many of `trades` recorded slippage — the slippage sum covers only those. */
  slippageTrades: number;
}

export type FliesTradeLog = Paged<FliesTradeLogRow> & { totals: FliesTradeLogTotals };

export function useFliesTradeLog(
  mode: TradingMode,
  outcome: string,
  search: string,
  page: PageState,
  era: string | null = null,
  range: { from: string | null; to: string | null } = { from: null, to: null },
  arm: string | null = null,
) {
  const params = new URLSearchParams({ mode, outcome, search });
  if (era !== null) params.set("era", era);
  if (arm !== null) params.set("arm", arm);
  if (range.from !== null) params.set("from", range.from);
  if (range.to !== null) params.set("to", range.to);
  pageParams(params, "", page);
  return useQuery<FliesTradeLog>({
    queryKey: ["flies-tradelog", mode, outcome, search, page, era, range.from, range.to, arm],
    queryFn: () => getJson<FliesTradeLog>(`/api/flies/tradelog?${params.toString()}`),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

/** The filter selects' own options, narrowed to the same era as the data — an option that selects
 *  nothing reads as "nothing happened" rather than "not in this era". */
export interface FliesMeta {
  arms: string[];
  dates: string[];
  symbols: string[];
  eras: Array<{ era: string; label: string; trades: number }>;
  currentEra: string;
}

export function useFliesMeta(mode: TradingMode, era: string | null = null) {
  return useQuery<FliesMeta>({
    queryKey: ["flies-meta", mode, era],
    queryFn: () =>
      getJson<FliesMeta>(`/api/flies/meta?mode=${mode}${era !== null ? `&era=${era}` : ""}`),
    staleTime: 300_000,
  });
}

/**
 * PMCC. No mode argument anywhere: the module is paper-only by construction, not by preference.
 */
/** `era`: null = the module's current era; "ALL" pools every era, a stated choice; or one era key.
 *  Only the arm comparison is era-scoped. */
export function usePmcc(era: string | null = null) {
  return useQuery<PmccPayload>({
    queryKey: ["pmcc", era],
    queryFn: () => getJson<PmccPayload>(`/api/pmcc${era === null ? "" : `?era=${encodeURIComponent(era)}`}`),
    // The loop marks every tick in session; 15s matches the other module dashboards.
    refetchInterval: 15_000,
    placeholderData: (prev) => prev,
  });
}

/**
 * One PMCC position, week by week (the tracker tab), bridged from the module's own analytics. A
 * position only changes when the loop marks or trades it, and the server answers an idle one from
 * memory, so a 30s poll is cheap.
 */
export function usePmccTracker(position: string | null) {
  return useQuery<PmccBridged<PmccTracker>>({
    queryKey: ["pmcc-tracker", position],
    queryFn: () => getJson<PmccBridged<PmccTracker>>(`/api/pmcc/tracker?position=${encodeURIComponent(position ?? "")}`),
    enabled: position !== null,
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });
}

export function usePmccTrackerIndex() {
  return useQuery<PmccBridged<PmccTrackerIndexRow[]>>({
    queryKey: ["pmcc-tracker-index"],
    queryFn: () => getJson<PmccBridged<PmccTrackerIndexRow[]>>("/api/pmcc/tracker/index"),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

/** The arms' weekly A/B: per (arm, symbol, ISO week), the change in net P&L. */
export function usePmccWeekly(era: string | null = null) {
  const q = era === null ? "" : `?era=${encodeURIComponent(era)}`;
  return useQuery<PmccBridged<PmccWeeklyRow[]>>({
    queryKey: ["pmcc-weekly", era],
    queryFn: () => getJson<PmccBridged<PmccWeeklyRow[]>>(`/api/pmcc/weekly${q}`),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export interface PmccHistoryFilter extends DateRangeQuery {
  arm: string | null;
  symbol: string | null;
}

export function usePmccHistory(filter: PmccHistoryFilter, page: PageState) {
  const params = new URLSearchParams();
  if (filter.arm !== null) params.set("arm", filter.arm);
  if (filter.symbol !== null) params.set("symbol", filter.symbol);
  rangeParams(params, filter);
  pageParams(params, "", page);
  return useQuery<PmccHistory>({
    queryKey: ["pmcc-history", filter, page],
    queryFn: () => getJson<PmccHistory>(`/api/pmcc/history?${params.toString()}`),
    // A cycle closes a few times a week at most — polling it hard would be noise.
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function usePmccMeta() {
  return useQuery<PmccMeta>({
    queryKey: ["pmcc-meta"],
    queryFn: () => getJson<PmccMeta>("/api/pmcc/meta"),
    staleTime: 300_000,
  });
}

/**
 * curve (VXX term-structure roll-yield harvest). No mode argument: paper-only by construction, the
 * same reasoning as pmcc's hook above.
 */
export function useCurve() {
  return useQuery<CurvePayload>({
    queryKey: ["curve"],
    queryFn: () => getJson<CurvePayload>("/api/curve"),
    refetchInterval: 15_000,
    placeholderData: (prev) => prev,
  });
}

export interface CurveHistoryFilter extends DateRangeQuery {
  arm: string | null;
  symbol: string | null;
}

export function useCurveHistory(filter: CurveHistoryFilter, page: PageState) {
  const params = new URLSearchParams();
  if (filter.arm !== null) params.set("arm", filter.arm);
  if (filter.symbol !== null) params.set("symbol", filter.symbol);
  rangeParams(params, filter);
  pageParams(params, "", page);
  return useQuery<CurveHistory>({
    queryKey: ["curve-history", filter, page],
    queryFn: () => getJson<CurveHistory>(`/api/curve/history?${params.toString()}`),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useCurveMeta() {
  return useQuery<CurveMeta>({
    queryKey: ["curve-meta"],
    queryFn: () => getJson<CurveMeta>("/api/curve/meta"),
    staleTime: 300_000,
  });
}

/**
 * bwb (SPX daily-laddered put broken-wing butterfly / 1-3-2 add-on trigger experiment). No mode
 * argument: paper-only by construction, the same reasoning as pmcc/curve above.
 */
export function useBwb() {
  return useQuery<BwbPayload>({
    queryKey: ["bwb"],
    queryFn: () => getJson<BwbPayload>("/api/bwb"),
    refetchInterval: 15_000,
    placeholderData: (prev) => prev,
  });
}

export interface BwbHistoryFilter extends DateRangeQuery {
  arm: string | null;
  symbol: string | null;
}

export function useBwbHistory(filter: BwbHistoryFilter, page: PageState) {
  const params = new URLSearchParams();
  if (filter.arm !== null) params.set("arm", filter.arm);
  if (filter.symbol !== null) params.set("symbol", filter.symbol);
  rangeParams(params, filter);
  pageParams(params, "", page);
  return useQuery<BwbHistory>({
    queryKey: ["bwb-history", filter, page],
    queryFn: () => getJson<BwbHistory>(`/api/bwb/history?${params.toString()}`),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useBwbMeta() {
  return useQuery<BwbMeta>({
    queryKey: ["bwb-meta"],
    queryFn: () => getJson<BwbMeta>("/api/bwb/meta"),
    staleTime: 300_000,
  });
}

// ---- collapsed decision journal (curve/pmcc/bwb; readers/decisions.ts) ----

export type DecisionsModule = "curve" | "pmcc" | "bwb";

export interface DecisionRow {
  arm: string;
  symbol: string;
  reason: string;
  accepted: boolean;
  occurrences: number;
  detail: string | null;
}

export interface DecisionsPayload {
  module: DecisionsModule;
  tradeDate: string | null;
  rows: DecisionRow[];
}

export function useDecisions(module: DecisionsModule) {
  return useQuery<DecisionsPayload>({
    queryKey: ["decisions", module],
    queryFn: () => getJson<DecisionsPayload>(`/api/${module}/decisions`),
    refetchInterval: 30_000,
  });
}

export interface PmccAssignmentRow extends PmccAssignment {
  positionId: string;
  symbol: string;
  assignedSession: string;
}

export function usePmccAssignments() {
  return useQuery<{ rows: PmccAssignmentRow[] }>({
    queryKey: ["pmcc-assignments"],
    queryFn: () => getJson<{ rows: PmccAssignmentRow[] }>("/api/pmcc/assignments"),
    refetchInterval: 60_000,
  });
}

export function useCalendars() {
  return useQuery<CalendarsPayload>({
    queryKey: ["calendars"],
    queryFn: () => getJson<CalendarsPayload>("/api/calendars"),
    // The loop marks every 30s in session; 15s matches the other module dashboards.
    refetchInterval: 15_000,
    placeholderData: (prev) => prev,
  });
}

export function useCalendarsWeeks(range: DateRangeQuery = NO_DATE_RANGE) {
  const params = new URLSearchParams();
  rangeParams(params, range);
  return useQuery<CalendarsWeeks>({
    queryKey: ["calendars-weeks", range.from, range.to],
    queryFn: () => getJson<CalendarsWeeks>(`/api/calendars/weeks?${params.toString()}`),
    // A week finishes once a week. Polling this hard would be noise.
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useCalendarsWeek(week: string | null) {
  return useQuery<{ rows: CalendarsPosition[] }>({
    queryKey: ["calendars-week", week],
    queryFn: () => getJson<{ rows: CalendarsPosition[] }>(`/api/calendars/week?week=${week ?? ""}`),
    enabled: week !== null,
    staleTime: 60_000,
  });
}

export function useCalendarsPolicies() {
  return useQuery<CalendarsPoliciesPayload>({
    queryKey: ["calendars-policies"],
    queryFn: () => getJson<CalendarsPoliciesPayload>("/api/calendars/policies"),
    // A replay over the whole mark path, memoised server-side; the answer only moves when a week
    // completes. Nothing here justifies a poll.
    staleTime: 300_000,
  });
}

export function useEarnings(
  trades: PageState,
  reviews: PageState,
  era: string | null = null,
  range: DateRangeQuery = NO_DATE_RANGE,
) {
  const params = new URLSearchParams();
  pageParams(params, "trades", trades);
  pageParams(params, "reviews", reviews);
  if (era !== null) params.set("era", era);
  rangeParams(params, range);
  return useQuery<EarningsPayload>({
    queryKey: ["earnings", trades, reviews, era, range.from, range.to],
    queryFn: () => getJson<EarningsPayload>(`/api/earnings?${params.toString()}`),
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });
}

export interface EarningsMark {
  markedAt: string | null;
  exitDebit: number | null;
  unrealizedPnl: number | null;
  spot: number | null;
  source: string | null;
  maxLegSpreadPct: number | null;
}

export interface EarningsEvent {
  orderId: string;
  occurredAt: string | null;
  phase: string | null;
  action: string;
  reason: string;
  executed: boolean;
  gate: string | null;
}

export interface EarningsOpenPosition {
  orderId: string;
  symbol: string;
  strategy: string;
  expiration: string | null;
  entryCredit: number | null;
  quantity: number | null;
  capitalAtRisk: number | null;
  openedAt: string | null;
  status: string | null;
  closeAttempts: number | null;
  maxUnrealizedPnl: number | null;
  minUnrealizedPnl: number | null;
  mark: EarningsMark | null;
  lastEvent: EarningsEvent | null;
}

export interface EarningsLivePayload {
  positions: EarningsOpenPosition[];
  events: EarningsEvent[];
  loop: {
    ranAt: string | null;
    phase: string | null;
    status: string | null;
    openPositions: number | null;
    marksWritten: number | null;
    actionsTaken: number | null;
    quotesFresh: number | null;
    quotesStale: number | null;
    openCapital: number | null;
    note: string | null;
  } | null;
  openCapital: number;
  generatedAt: string;
}

/** Open earnings positions and the managed loop's own vital signs. Polled at the loop's own
 *  cadence — a faster poll would only redraw the same minute's marks. */
export function useEarningsLive() {
  return useQuery<EarningsLivePayload>({
    queryKey: ["earnings-live"],
    queryFn: () => getJson<EarningsLivePayload>("/api/earnings/live"),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export interface SymbolAnalysis {
  symbol: string;
  bars: Array<{ t: number; o: number; h: number; l: number; c: number; v: number }>;
  overlays: Record<string, Array<number | null>>;
  levels: Array<{ price: number; kind: "support" | "resistance"; touches: number }>;
  trend: { "1m": string | null; "6m": string | null };
}

export interface BlacklistRow {
  symbol: string;
  reason: string;
  addedAt: string;
}

export interface ChainEodStatus {
  latest: { tradeDate: string; symbols: number } | null;
  running: boolean;
}

export interface CollectorsPayload {
  dx: string;
  etDate: string;
  candles: {
    running: boolean;
    progress: { done: number; total: number } | null;
    lastResult: { warmed: number; failed: number; finishedAt: number } | null;
  };
  chain: {
    latest: { tradeDate: string; symbols: number } | null;
    running: boolean;
    progress: { done: number; total: number } | null;
    lastResult: { tradeDate: string; captured: number; skipped: number; finishedAt: number } | null;
  };
}

/**
 * The integrity block alone, for the strip that rides every tab.
 *
 * Separate from `useGex` because that one is gated to the history tab -- it pages the regime table,
 * and fetching 100 rows every ten seconds on tabs that do not show them would be waste. This asks
 * for `limit=1` (2.9KB against 21KB) and is always enabled, because a stale flip is exactly what a
 * reader of the CHART tab needs to know and that is the tab where the table is not wanted.
 */
export function useGexIntegrity() {
  return useQuery<GexPayload>({
    queryKey: ["gex-integrity"],
    queryFn: () => getJson<GexPayload>("/api/gex?limit=1"),
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });
}

export function useGex(enabled = true, page: PageState = FIRST_PAGE) {
  const params = new URLSearchParams();
  pageParams(params, "", page);
  return useQuery<GexPayload>({
    queryKey: ["gex", page],
    queryFn: () => getJson<GexPayload>(`/api/gex?${params.toString()}`),
    refetchInterval: 10_000,
    enabled,
    placeholderData: (prev) => prev,
  });
}

// ---- shared module performance (server/src/readers/performance.ts) ----
//
// Types live in @console/shared (types/performance.ts) -- server/src/readers/performance.ts's
// readModulePerformance is the source of truth both sides import against. Field names inside
// `reading` stay snake_case, matching core.metrics' own calibration_reading JSON verbatim
// (readModuleMetrics's own convention, kept through the bridge and the route) rather than a
// ~20-key hand mapping to camelCase.

export type {
  PerformanceModuleId,
  ModulePerformanceGroup,
  ExitReasonRow,
  HeldBackRow,
  AdvisedPair,
  MeasurementBreak,
  ExcursionPosition,
  ExcursionsDistribution,
  ExcursionsData,
  ExcursionsResult,
  ModulePerformanceResult,
} from "@console/shared";
import type { ModulePerformanceResult, PerformanceModuleId } from "@console/shared";
import type { OpeningRangePayload, RegimeCutsModule, RegimeCutsPayload } from "@console/shared";

/** One module's calibration reading, exit reasons, advised pairs and measurement breaks in one
 * request (`GET /api/performance/:module`). `era="current"` (the default) bounds to the suite's own
 * data_epoch, matching every other era-scoped surface's default (`readers/meic.ts::CURRENT_ERA`,
 * ...); `era="ALL"` pools every session on file. */
export function useModulePerformance(
  module: PerformanceModuleId,
  era: "current" | "ALL" = "current",
  mode: TradingMode = "paper",
) {
  return useQuery<ModulePerformanceResult>({
    queryKey: ["performance", module, era, mode],
    queryFn: () => getJson<ModulePerformanceResult>(`/api/performance/${module}?era=${era}&mode=${mode}`),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}


/** The regime-cuts artifact for one module (`GET /api/<module>/regime-cuts[?session=]`): the
 * module's own nightly per-arm x per-regime cut, rendered without recomputation. `session`
 * null = the latest artifact. Polled slowly: the file changes once a day. */
/** The 09:30-10:00 window for one session. Polls faster than the artifact readers because it is
 *  live during the half-hour it describes, and settles the moment the entry window opens. */
export function useOpeningRange(session: string | null = null) {
  const qs = session === null ? "" : `?session=${encodeURIComponent(session)}`;
  return useQuery<OpeningRangePayload>({
    queryKey: ["opening-range", session],
    queryFn: () => getJson<OpeningRangePayload>(`/api/flies/opening-range${qs}`),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useRegimeCuts(module: RegimeCutsModule, session: string | null = null) {
  const qs = session === null ? "" : `?session=${encodeURIComponent(session)}`;
  return useQuery<RegimeCutsPayload>({
    queryKey: ["regime-cuts", module, session],
    queryFn: () => getJson<RegimeCutsPayload>(`/api/${module}/regime-cuts${qs}`),
    refetchInterval: 300_000,
    placeholderData: (prev) => prev,
  });
}
