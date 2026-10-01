import type { AlertDaemonHealth } from "@console/shared";
import { useMorningReport, useOverview } from "../../lib/api";
import { LiveFliesPanel } from "./LiveFliesPanel";
import { useBookRotation } from "../../lib/useBookRotation";
import { useSystem } from "./SuiteCards";
import { ExposureCard, EntriesCard } from "./DeskCards";

/**
 * The suite's morning-to-close picture, redesigned (2026-09) to fit 1440×900 with no page
 * scroll: the suite matrix (exposure + entries) on the left, and on the right the flies live
 * pilot's latest session (`LiveFliesPanel`), which replaced the suite equity curve, the session
 * heatmap and the end-of-day table on 2026-10-01. Logs, watchdog, morning phase and the per-producer liveness strip are
 * in the shell's status bar (`components/shell/StatusBar`), on every page since 2026-10-01, when
 * the evidence-clock chip row also went. See `docs/history/` for what the taller card-stack
 * layout it replaces looked like.
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

function phaseChipClass(phase: string): string {
  if (phase === "green") return "chip-ok";
  if (phase === "yellow") return "chip-warn";
  if (phase === "red") return "chip-missing";
  return "chip";
}

/** The morning pack's phase: the day's first verdict on the market. */
function MorningChip() {
  const phase = useMorningReport().data?.current?.phase ?? null;
  if (phase === null) return null;
  return (
    <span className={`chip ${phaseChipClass(phase.phase)}`}>
      morning {phase.phase.toUpperCase()}
      {phase.gatesMeasured !== null &&
        phase.gatesTotal !== null &&
        ` · ${String(phase.gatesMet ?? 0)} of ${String(phase.gatesTotal)} measured gates met`}
    </span>
  );
}

export function OverviewPage() {
  // One clock for both desk cards, so they always show the same book.
  const rotation = useBookRotation();
  const { isError } = useOverview();
  const { data: system } = useSystem();
  const liveCount = system?.modules.filter((m) => m.liveTrading === true).length ?? 0;

  return (
    <div className="page overview-page">
      <div className="page-title-row">
        <h1>Overview</h1>
        {/* Every chip sits right, opposite the heading. */}
        <div className="page-title-chips">
          {isError && <span className="chip chip-missing">console API unreachable</span>}
          {liveCount > 0 && <span className="chip chip-missing">{liveCount} module{liveCount === 1 ? "" : "s"} LIVE</span>}
          {system && (
            <span className={`chip ${system.halted.active ? "chip-missing" : "chip-ok"}`}>
              {system.halted.active ? "LIVE HALTED" : "halt flag clear"}
            </span>
          )}
          {system?.alertDaemon != null && <AlertDaemonChip health={system.alertDaemon} />}
          <MorningChip />
        </div>
      </div>

      <div className="overview-body">
        <div className="overview-left">
          <ExposureCard rotation={rotation} />
          <EntriesCard rotation={rotation} />
        </div>
        <div className="overview-right">
          <LiveFliesPanel />
        </div>
      </div>

    </div>
  );
}
