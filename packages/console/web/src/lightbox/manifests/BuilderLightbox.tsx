import { BuilderPage } from "../../pages/Builder/BuilderPage";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * The Builder (#1180): a page of its own, apart from Backtest (a forward test is not a backtest, and
 * Backtest's licensed pieces are gated). This first tab reads every automation block by block from the
 * automations package: each block's backtest label, the start and forward rule written at enrolment,
 * and whether our own data covers a backtest. Each card's editor checks an automation with the module
 * and saves it, always off, through the orchestrator's config editor (`BuilderEditor`).
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
