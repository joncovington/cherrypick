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
  SymbolCardPayload,
} from "@console/shared";

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

let csrfToken: string | null = null;

async function getCsrf(): Promise<string> {
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

export interface MeicTradeQuery {
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
  if (q.symbol !== null) params.set("symbol", q.symbol);
  if (q.profile !== null) params.set("profile", q.profile);
  if (q.era !== null) params.set("era", q.era);
  if (q.reason !== null) params.set("reason", q.reason);
  return useQuery<MeicPayload>({
    queryKey: ["meic", mode, q.symbol, q.profile, q.era, q.outcome, q.reason, q.search, q.limit, q.offset],
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
  symbol: string;
  arm: string | null;
  entryMode: string | null;
  kind: string | null;
  side: string | null;
  center: number | null;
  window: string | null;
  net: number | null;
  fees: number | null;
  pnl: number | null;
  latencyMin: number | null;
  pinned: boolean;
}

export function useFliesTradeLog(mode: TradingMode, outcome: string, search: string, page: PageState) {
  const params = new URLSearchParams({ mode, outcome, search });
  pageParams(params, "", page);
  return useQuery<Paged<FliesTradeLogRow>>({
    queryKey: ["flies-tradelog", mode, outcome, search, page],
    queryFn: () => getJson<Paged<FliesTradeLogRow>>(`/api/flies/tradelog?${params.toString()}`),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

/** The filter selects' own options, narrowed to the same era as the data — an option that selects
 *  nothing reads as "nothing happened" rather than "not in this era". */
export function useFliesMeta(mode: TradingMode, era: string | null = null) {
  return useQuery<{ arms: string[]; dates: string[]; symbols: string[] }>({
    queryKey: ["flies-meta", mode, era],
    queryFn: () =>
      getJson<{ arms: string[]; dates: string[]; symbols: string[] }>(
        `/api/flies/meta?mode=${mode}${era !== null ? `&era=${era}` : ""}`,
      ),
    staleTime: 300_000,
  });
}

export function useEarnings(trades: PageState, reviews: PageState) {
  const params = new URLSearchParams();
  pageParams(params, "trades", trades);
  pageParams(params, "reviews", reviews);
  return useQuery<EarningsPayload>({
    queryKey: ["earnings", trades, reviews],
    queryFn: () => getJson<EarningsPayload>(`/api/earnings?${params.toString()}`),
    refetchInterval: 30_000,
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

export function useWatchlist() {
  return useQuery<{ symbols: string[]; rows: TtWatchlistRow[] }>({
    queryKey: ["watchlist"],
    queryFn: () => getJson<{ symbols: string[]; rows: TtWatchlistRow[] }>("/api/watchlist"),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useTtWatchlists() {
  return useQuery<TtWatchlistIndex & { lastError: string | null }>({
    queryKey: ["tt-watchlists"],
    queryFn: () => getJson<TtWatchlistIndex & { lastError: string | null }>("/api/tt-watchlists"),
    refetchInterval: 60_000,
  });
}

export function useTtWatchlist(key: string | null) {
  return useQuery<TtWatchlistPayload>({
    queryKey: ["tt-watchlist", key],
    queryFn: () => getJson<TtWatchlistPayload>(`/api/tt-watchlists/${encodeURIComponent(key!)}`),
    enabled: key !== null,
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });
}

export interface BlacklistRow {
  symbol: string;
  reason: string;
  addedAt: string;
}

export function useBlacklist() {
  return useQuery<{ rows: BlacklistRow[] }>({
    queryKey: ["blacklist"],
    queryFn: () => getJson<{ rows: BlacklistRow[] }>("/api/blacklist"),
  });
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

export function useCollectors() {
  return useQuery<CollectorsPayload>({
    queryKey: ["collectors"],
    queryFn: () => getJson<CollectorsPayload>("/api/collectors"),
    refetchInterval: 10_000,
  });
}

export function useChainEodStatus() {
  return useQuery<ChainEodStatus>({
    queryKey: ["chain-eod-status"],
    queryFn: () => getJson<ChainEodStatus>("/api/chain-eod/status"),
    refetchInterval: 60_000,
  });
}

export function useSymbolCard(symbol: string) {
  const valid = /^[A-Z][A-Z0-9./]{0,9}$/.test(symbol);
  return useQuery<SymbolCardPayload>({
    queryKey: ["symbol-card", symbol],
    queryFn: () => getJson<SymbolCardPayload>(`/api/symbol-card/${encodeURIComponent(symbol)}`),
    enabled: valid,
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useSymbolAnalysis(symbol: string) {
  return useQuery<SymbolAnalysis>({
    queryKey: ["symbol", symbol],
    queryFn: () => getJson<SymbolAnalysis>(`/api/symbol/${symbol}`),
    retry: false,
  });
}

export function useGex() {
  return useQuery<GexPayload>({
    queryKey: ["gex"],
    queryFn: () => getJson<GexPayload>("/api/gex"),
    refetchInterval: 10_000,
  });
}
