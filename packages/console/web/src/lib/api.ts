import { useQuery } from "@tanstack/react-query";
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
  PmccPayload,
  PmccCycleRow,
  PmccMeta,
  PmccAssignment,
  CurvePayload,
  CurveCycleRow,
  CurveMeta,
  BwbPayload,
  BwbCycleRow,
  BwbMeta,
  CalendarsPayload,
  CalendarsPoliciesPayload,
  CalendarsPosition,
  CalendarsWeekRow,
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

export function useReview(session?: string) {
  return useQuery<ReviewPayload>({
    queryKey: ["review", session ?? "latest"],
    queryFn: () => getJson<ReviewPayload>(`/api/review${session ? `?session=${session}` : ""}`),
    // The fact set changes twice a day, not continuously — polling it hard would be noise.
    refetchInterval: 60_000,
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

export function useAdvisor(session?: string) {
  return useQuery<AdvisorPayload>({
    queryKey: ["advisor", session ?? "latest"],
    queryFn: () => getJson<AdvisorPayload>(`/api/advisor${session ? `?session=${session}` : ""}`),
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

export interface MeicTradeQuery {
  /** null = the latest session, resolved server-side like every other Today card. */
  day: string | null;
  symbol: string | null;
  profile: string | null;
  era: string | null;
  outcome: string;
  reason: string | null;
  search: string;
  limit: number;
  offset: number;
}

export function useMeic(mode: TradingMode, q: MeicTradeQuery) {
  const params = new URLSearchParams({
    mode,
    outcome: q.outcome,
    search: q.search,
    limit: String(q.limit),
    offset: String(q.offset),
  });
  if (q.day !== null) params.set("date", q.day);
  if (q.symbol !== null) params.set("symbol", q.symbol);
  if (q.profile !== null) params.set("profile", q.profile);
  if (q.era !== null) params.set("era", q.era);
  if (q.reason !== null) params.set("reason", q.reason);
  return useQuery<MeicPayload>({
    queryKey: ["meic", mode, q.day, q.symbol, q.profile, q.era, q.outcome, q.reason, q.search, q.limit, q.offset],
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
  net: number | null;
  fees: number | null;
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
 * PMCC-99. No mode argument anywhere: the module is paper-only by construction, not by preference.
 */
export function usePmcc() {
  return useQuery<PmccPayload>({
    queryKey: ["pmcc"],
    queryFn: () => getJson<PmccPayload>("/api/pmcc"),
    // The loop marks every tick in session; 15s matches the other module dashboards.
    refetchInterval: 15_000,
    placeholderData: (prev) => prev,
  });
}

export interface PmccHistoryFilter {
  book: string | null;
  symbol: string | null;
}

export function usePmccHistory(filter: PmccHistoryFilter, page: PageState) {
  const params = new URLSearchParams();
  if (filter.book !== null) params.set("book", filter.book);
  if (filter.symbol !== null) params.set("symbol", filter.symbol);
  pageParams(params, "", page);
  return useQuery<Paged<PmccCycleRow>>({
    queryKey: ["pmcc-history", filter, page],
    queryFn: () => getJson<Paged<PmccCycleRow>>(`/api/pmcc/history?${params.toString()}`),
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

export interface CurveHistoryFilter {
  book: string | null;
  symbol: string | null;
}

export function useCurveHistory(filter: CurveHistoryFilter, page: PageState) {
  const params = new URLSearchParams();
  if (filter.book !== null) params.set("book", filter.book);
  if (filter.symbol !== null) params.set("symbol", filter.symbol);
  pageParams(params, "", page);
  return useQuery<Paged<CurveCycleRow>>({
    queryKey: ["curve-history", filter, page],
    queryFn: () => getJson<Paged<CurveCycleRow>>(`/api/curve/history?${params.toString()}`),
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

export interface BwbHistoryFilter {
  book: string | null;
  symbol: string | null;
}

export function useBwbHistory(filter: BwbHistoryFilter, page: PageState) {
  const params = new URLSearchParams();
  if (filter.book !== null) params.set("book", filter.book);
  if (filter.symbol !== null) params.set("symbol", filter.symbol);
  pageParams(params, "", page);
  return useQuery<Paged<BwbCycleRow>>({
    queryKey: ["bwb-history", filter, page],
    queryFn: () => getJson<Paged<BwbCycleRow>>(`/api/bwb/history?${params.toString()}`),
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

export function useCalendarsWeeks() {
  return useQuery<{ rows: CalendarsWeekRow[] }>({
    queryKey: ["calendars-weeks"],
    queryFn: () => getJson<{ rows: CalendarsWeekRow[] }>("/api/calendars/weeks"),
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

export function useEarnings(trades: PageState, reviews: PageState, era: string | null = null) {
  const params = new URLSearchParams();
  pageParams(params, "trades", trades);
  pageParams(params, "reviews", reviews);
  if (era !== null) params.set("era", era);
  return useQuery<EarningsPayload>({
    queryKey: ["earnings", trades, reviews, era],
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

