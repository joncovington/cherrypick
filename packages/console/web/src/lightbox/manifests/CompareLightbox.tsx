import { ComparePage } from "../../pages/Compare/ComparePage";
import { RiskTab } from "../../pages/Compare/RiskTab";
import { CalendarTab } from "../../pages/Compare/CalendarTab";
import { ModuleFrame } from "../ModuleFrame";
import { ModeToggle } from "../../components/ModeToggle";
import { useMode } from "../../lib/useMode";
import type { SlideDef } from "../types";

/**
 * Compare (2026-10-12): every arm in the suite beside its control, one section per module, from
 * `core.compare`. One tab; the scope, sort and open arm ride in the query string, which the frame
 * carries across.
 */
const slides: SlideDef[] = [
  { id: "arms", label: "arms", render: () => <ComparePage /> },
  { id: "risk", label: "risk", render: () => <RiskTab /> },
  { id: "calendar", label: "calendar", render: () => <CalendarTab /> },
];

export function CompareLightbox({ slide }: { slide: string }) {
  const [mode, setMode] = useMode();
  // Risk and calendar read paper or live ledgers (meic, flies, earnings and bwb keep a live one);
  // the arms tab compares paper arms only, so it shows no toggle.
  const moded = slide === "risk" || slide === "calendar";
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
            {slide === "risk" ? "source: core.analysis" : slide === "calendar" ? "source: core.metrics" : "source: core.compare"}
          </span>
          {moded && <ModeToggle mode={mode} onChange={setMode} />}
        </>
      }
    />
  );
}
