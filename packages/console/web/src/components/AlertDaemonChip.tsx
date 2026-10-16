import type { AlertDaemonHealth } from "@console/shared";
import { useQuery } from "@tanstack/react-query";

/**
 * The flies order-alert daemon's health, on the Live page's header (2026-10-16; it sat on the
 * Overview's title row until that page was removed). Read off `/api/system`, which carries the
 * daemon's pid and status files (`services/alertDaemon.ts`).
 */
export function useAlertDaemon() {
  return useQuery<AlertDaemonHealth | null>({
    queryKey: ["system", "alert-daemon"],
    queryFn: async () => {
      const res = await fetch("/api/system");
      if (!res.ok) throw new Error(`system: HTTP ${res.status}`);
      return ((await res.json()) as { alertDaemon: AlertDaemonHealth | null }).alertDaemon;
    },
    refetchInterval: 60_000,
  });
}

function ago(seconds: number | null): string {
  if (seconds === null) return "never";
  if (seconds < 90) return `${String(seconds)}s ago`;
  if (seconds < 5400) return `${String(Math.round(seconds / 60))}m ago`;
  return `${String(Math.round(seconds / 3600))}h ago`;
}

const RESTART =
  "restart: python -m cherrypick.flies.alert_daemon --stop, then pythonw -m cherrypick.flies.alert_daemon (or re-run /live-flies-start)";

/**
 * Amber, never red: the daemon only makes fills get noticed sooner, and without it the loop still
 * confirms them on its own poll.
 */
export function AlertDaemonChip() {
  const health = useAlertDaemon().data;
  if (health == null) return null;
  const beat = ago(health.ageSeconds);
  const seen = health.alertsSeen !== null ? ` · ${String(health.alertsSeen)} alert${health.alertsSeen === 1 ? "" : "s"} this run` : "";
  const view = {
    ok: { cls: "chip chip-ok", text: "alert daemon ok", title: `flies order-alert daemon running (pid ${String(health.pid)}), heartbeat ${beat}${seen}` },
    stale: {
      cls: "chip chip-warn",
      text: `alert daemon stale · ${beat}`,
      title: `the process is alive but has not heartbeat for ${beat} — the broker websocket may have died silently. Fills are still confirmed by the loop's own poll, just later. ${RESTART}`,
    },
    down: {
      cls: "chip chip-warn",
      text: "alert daemon down",
      title: `not running while flies is armed for today (last heartbeat ${beat}). Fills are still confirmed by the loop's own poll, just later. ${RESTART}`,
    },
    off: {
      cls: "chip",
      text: "alert daemon off",
      title: "not running, and flies is not armed today, so nothing expects it — /live-flies-start starts it, and it exits at the disarm time",
    },
  }[health.state];
  return (
    <span className={view.cls} title={view.title}>
      {view.text}
    </span>
  );
}
