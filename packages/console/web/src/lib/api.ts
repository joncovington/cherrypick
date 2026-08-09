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

export function useMeic(mode: TradingMode) {
  return useQuery<MeicPayload>({
    queryKey: ["meic", mode],
    queryFn: () => getJson<MeicPayload>(`/api/meic?mode=${mode}`),
    refetchInterval: 15_000,
  });
}

export function useFlies(mode: TradingMode) {
  return useQuery<FliesPayload>({
    queryKey: ["flies", mode],
    queryFn: () => getJson<FliesPayload>(`/api/flies?mode=${mode}`),
    refetchInterval: 15_000,
  });
}

export function useEarnings() {
  return useQuery<EarningsPayload>({
    queryKey: ["earnings"],
    queryFn: () => getJson<EarningsPayload>("/api/earnings"),
    refetchInterval: 30_000,
  });
}

export function useGex() {
  return useQuery<GexPayload>({
    queryKey: ["gex"],
    queryFn: () => getJson<GexPayload>("/api/gex"),
    refetchInterval: 10_000,
  });
}
