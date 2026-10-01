import type { AlertDaemonHealth } from "@console/shared";
import { useOverview } from "../../lib/api";
import { EquityCard } from "./EquityCard";
import { EquityBottomRow } from "./EquityBottomRow";
import { useSystem } from "./SuiteCards";
import { ExposureCard, EntriesCard, EvidenceClockRow } from "./DeskCards";
import { StatusBar } from "./StatusBar";
import { FuturesTicker } from "./FuturesTicker";

/**
 * The suite's morning-to-close picture, redesigned (2026-09) to fit 1440×900 with no page
 * scroll: the suite matrix (exposure + entries) on the left, equity + session heatmap +
 * end-of-day on the right, an evidence-clock chip row, and live quotes / system / logs /
 * watchdog·session·morning-phase·halt demoted to a one-line status bar with a drawer. The
 * per-producer liveness strip lives in the global header (`StatusHeader`) beside the clock, not
 * on this page alone. See `docs/history/` for what the taller card-stack layout it replaces
 * looked like.
 */
function ago(seconds: number | null): string {
  if (seconds === null) return "never";
  if (seconds < 90) return `${String(seconds)}s ago`;
  if (seconds < 5400) return `${String(Math.round(seconds / 60))}m ago`;
  return `${String(Math.round(seconds / 3600))}h ago`;
}

const RESTART =
  "restart: python -m cherrypick.flies.alert_daemon --stop, then pythonw -m cherrypick.flies.alert_daemon (or re-run /live-flies-start)";

/**
 * The flies order-alert daemon beside the live chip. Amber, never red: the daemon only makes fills
 * get noticed sooner, and without it the loop still confirms them on its own poll.
 */
function AlertDaemonChip({ health }: { health: AlertDaemonHealth }) {
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

export function OverviewPage() {
  const { isError } = useOverview();
  const { data: system } = useSystem();
  const liveCount = system?.modules.filter((m) => m.liveTrading === true).length ?? 0;

  return (
    <div className="page overview-page">
      <div className="page-title-row">
        <h1>Overview</h1>
        {liveCount > 0 && <span className="chip chip-missing">{liveCount} module{liveCount === 1 ? "" : "s"} LIVE</span>}
        {system?.alertDaemon != null && <AlertDaemonChip health={system.alertDaemon} />}
        {isError && <span className="chip chip-missing">console API unreachable</span>}
        <FuturesTicker />
      </div>

      <div className="overview-body">
        <div className="overview-left">
          <ExposureCard />
          <EntriesCard />
        </div>
        <div className="overview-right">
          <EquityCard>
            <EquityBottomRow />
          </EquityCard>
        </div>
      </div>

      <EvidenceClockRow />
      <StatusBar />
    </div>
  );
}
