import type { AlertDaemonHealth } from "@console/shared";
import { useQuery } from "@tanstack/react-query";

interface SystemPanel {
  timezone: string | null;
  modules: Array<{ id: string; enabled: boolean; kind: string | null; streamer: boolean | null; liveTrading: boolean | null }>;
  services: Array<{
    id: string;
    enabled: boolean;
    autoRestart: boolean;
    launched: string | null;
    pid: number | null;
    health: string | null;
    note: string | null;
    detail: string | null;
  }>;
  watchdog: { intervalMinutes: number | null; renotifyMinutes: number | null; drawdownGuard: boolean | null };
  notify: { channels: string[]; tradeChannels: string[]; webhookStatus: string | null };
  halted: { active: boolean; path: string };
  alertDaemon: AlertDaemonHealth | null;
}

/** Shared with the page-title row's halt/live chips, so both read one fetch of the same data. */
export function useSystem() {
  return useQuery<SystemPanel>({
    queryKey: ["system"],
    queryFn: async () => {
      const res = await fetch("/api/system");
      if (!res.ok) throw new Error(`system: HTTP ${res.status}`);
      return (await res.json()) as SystemPanel;
    },
    refetchInterval: 60_000,
  });
}
