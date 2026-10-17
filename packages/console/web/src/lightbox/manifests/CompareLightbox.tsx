import { ComparePage } from "../../pages/Compare/ComparePage";
import { RiskTab } from "../../pages/Compare/RiskTab";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * Compare (2026-10-12): every arm in the suite beside its control, one section per module, from
 * `core.compare`. One tab; the scope, sort and open arm ride in the query string, which the frame
 * carries across.
 */
const slides: SlideDef[] = [
  { id: "arms", label: "arms", render: () => <ComparePage /> },
  { id: "risk", label: "risk", render: () => <RiskTab /> },
];

export function CompareLightbox({ slide }: { slide: string }) {
  return (
    <ModuleFrame
      module="compare"
      slide={slide}
      slides={slides}
      session={null}
      headerControls={
        <span
          className="chip"
          title="arms: python -m cherrypick.core.compare (core.ledgers rows, core.stats figures); risk: python -m cherrypick.core.analysis"
        >
          {slide === "risk" ? "source: core.analysis" : "source: core.compare"}
        </span>
      }
    />
  );
}
