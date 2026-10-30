import { ComparePage } from "../../pages/Compare/ComparePage";
import { RiskTab } from "../../pages/Compare/RiskTab";
import { CalendarTab } from "../../pages/Compare/CalendarTab";
import { PortfolioTab } from "../../pages/Compare/PortfolioTab";
import { SlicesTab } from "../../pages/Compare/SlicesTab";
import { ModuleFrame } from "../ModuleFrame";
import { ModeToggle } from "../../components/ModeToggle";
import { useMode } from "../../lib/useMode";
import type { SlideDef } from "../types";

/**
 * Compare (2026-10-12): every arm in the suite beside its control, one section per module, from
 * `core.compare`. One tab; the scope, sort and open arm ride in the query string, which the frame
 * carries across.
 */
export const slides: SlideDef[] = [
  { id: "arms", label: "arms", render: () => <ComparePage /> },
  { id: "risk", label: "risk", render: () => <RiskTab /> },
  { id: "calendar", label: "calendar", render: () => <CalendarTab /> },
  { id: "portfolio", label: "portfolio", render: () => <PortfolioTab /> },
  // B5: an arm's in-sample slice report (`technicals slices`); never a gate. Fails closed on backtest.
  { id: "slices", label: "slices", render: () => <SlicesTab /> },
];

export function CompareLightbox({ slide }: { slide: string }) {
  const [mode, setMode] = useMode();
  // Risk and calendar read paper or live ledgers (meic, flies, earnings and bwb keep a live one);
  // the arms tab compares paper arms only, so it shows no toggle.
  const moded = slide === "risk" || slide === "calendar" || slide === "portfolio";
  return (
    <ModuleFrame
      module="compare"
      slide={slide}
      slides={slides}
      session={null}
      headerControls={
        <>
          <span
            className="chip"
            title="arms: python -m cherrypick.core.compare (core.ledgers rows, core.stats figures); risk: python -m cherrypick.core.analysis"
          >
            {slide === "risk" ? "source: core.analysis" : slide === "calendar" ? "source: core.metrics" : slide === "portfolio" ? "source: core.portfolio" : "source: core.compare"}
          </span>
          {moded && <ModeToggle mode={mode} onChange={setMode} />}
        </>
      }
    />
  );
}
