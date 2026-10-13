import { ComparePage } from "../../pages/Compare/ComparePage";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * Compare (2026-10-12): every arm in the suite beside its control, one section per module, from
 * `core.compare`. One tab; the scope, sort and open arm ride in the query string, which the frame
 * carries across.
 */
const slides: SlideDef[] = [{ id: "arms", label: "arms", render: () => <ComparePage /> }];

export function CompareLightbox({ slide }: { slide: string }) {
  return (
    <ModuleFrame
      module="compare"
      slide={slide}
      slides={slides}
      session={null}
      headerControls={
        <span className="chip" title="python -m cherrypick.core.compare: core.ledgers rows, core.stats figures">
          source: core.compare
        </span>
      }
    />
  );
}
