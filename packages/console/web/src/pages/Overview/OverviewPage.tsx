import { useOverview } from "../../lib/api";
import { EquityCard } from "./EquityCard";
import { EquityBottomRow } from "./EquityBottomRow";
import { useSystem } from "./SuiteCards";
import { ExposureCard, EntriesCard, EvidenceClockRow } from "./DeskCards";
import { EarningsScreenCard } from "./EarningsScreenCard";
import { StatusBar } from "./StatusBar";

/**
 * The suite's morning-to-close picture, redesigned (2026-09) to fit 1440×900 with no page
 * scroll: the suite matrix (exposure + entries) on the left, equity + session heatmap +
 * end-of-day on the right, an evidence-clock chip row, and live quotes / system / logs /
 * watchdog·session·morning-phase·halt demoted to a one-line status bar with a drawer. The
 * per-producer liveness strip lives in the global header (`StatusHeader`) beside the clock, not
 * on this page alone. See `docs/history/` for what the taller card-stack layout it replaces
 * looked like.
 */
export function OverviewPage() {
  const { isError } = useOverview();
  const { data: system } = useSystem();
  const liveCount = system?.modules.filter((m) => m.liveTrading === true).length ?? 0;

  return (
    <div className="page overview-page">
      <div className="page-title-row">
        <h1>Overview</h1>
        {liveCount > 0 && <span className="chip chip-missing">{liveCount} module{liveCount === 1 ? "" : "s"} LIVE</span>}
        {isError && <span className="chip chip-missing">console API unreachable</span>}
      </div>

      <div className="overview-body">
        <div className="overview-left">
          <ExposureCard />
          <EntriesCard />
          <EarningsScreenCard />
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
