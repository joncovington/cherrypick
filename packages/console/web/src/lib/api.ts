import { useQuery } from "@tanstack/react-query";
import type {
  OverviewPayload,
  StatusPayload,
  MeicPayload,
  FliesPayload,
  EarningsPayload,
  GexPayload,
  TradingMode,
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
}

export function fliesQuery(mode: TradingMode, filter: FliesFilter): string {
  const params = new URLSearchParams({ mode });
  if (filter.arm !== null) params.set("arm", filter.arm);
  if (filter.date !== null) params.set("date", filter.date);
  return params.toString();
}

export function useFlies(mode: TradingMode, filter: FliesFilter) {
  return useQuery<FliesPayload>({
    queryKey: ["flies", mode, filter],
    queryFn: () => getJson<FliesPayload>(`/api/flies?${fliesQuery(mode, filter)}`),
    refetchInterval: 15_000,
  });
}

export function useFliesMeta(mode: TradingMode) {
  return useQuery<{ arms: string[]; dates: string[] }>({
    queryKey: ["flies-meta", mode],
    queryFn: () => getJson<{ arms: string[]; dates: string[] }>(`/api/flies/meta?mode=${mode}`),
    staleTime: 300_000,
  });
}

export function useEarnings() {
  return useQuery<EarningsPayload>({
    queryKey: ["earnings"],
    queryFn: () => getJson<EarningsPayload>("/api/earnings"),
    refetchInterval: 30_000,
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
  return useQuery<{ symbols: string[] }>({
    queryKey: ["watchlist"],
    queryFn: () => getJson<{ symbols: string[] }>("/api/watchlist"),
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
