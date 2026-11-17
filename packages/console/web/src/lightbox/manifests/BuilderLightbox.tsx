import { BuilderPage } from "../../pages/Builder/BuilderPage";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * The Builder (#1180): a page of its own, apart from Backtest (a forward test is not a backtest, and
 * Backtest's licensed pieces are gated). One automation at a time, built in five sections from the
 * module's own schema, beside the engine's decision of it today and its explore runs (`BuilderPage`).
 */
export const slides: SlideDef[] = [{ id: "automations", label: "automations", render: () => <BuilderPage /> }];

export function BuilderLightbox({ slide }: { slide: string }) {
  return (
    <ModuleFrame
      module="builder"
      slide={slide}
      slides={slides}
      session={null}
      headerControls={
        <span className="chip" title="python -m cherrypick.automations.cli builder">
          source: automations builder
        </span>
      }
    />
  );
}
