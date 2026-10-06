import type { TradingMode } from "@console/shared";
import { useModuleCosts } from "../../lib/api";
import { CostsView } from "./CostsView";

const NOTES = {
  meic: {
    premium: "net credit x multiplier x quantity, closed condors",
    gates:
      "MEIC enters many times a session, so a session counts as entered on its first fill and the fills column says how many there were.",
  },
  flies: {
    premium: "net credit x 100 x quantity, settled flies (a debit fly adds none)",
    gates:
      "Flies enters many times a session, so a session counts as entered on its first fill and the fills column says how many there were. cadence_blocked is the pacing rule doing its job.",
  },
} as const;

/** The costs page for a 0DTE module whose modelled fill already concedes slippage (meic, flies),
 *  on the page's own paper/live mode and era. */
export function ModuleCosts({ module, mode, era }: { module: "meic" | "flies"; mode: TradingMode; era: string | null }) {
  const costs = useModuleCosts(module, mode, era);
  return (
    <div className="cards cards-wide">
      <CostsView
        arms={costs.data?.arms ?? []}
        slippageInGross={costs.data?.slippageInGross ?? true}
        since={costs.data?.since ?? null}
        sinceLabel="the era's start"
        sinceRows={costs.data?.entryOutcomes ?? []}
        allRows={costs.data?.entryOutcomesAll ?? []}
        loading={costs.isLoading}
        premiumNote={NOTES[module].premium}
        gateNote={NOTES[module].gates}
        updatedAt={costs.dataUpdatedAt}
      />
    </div>
  );
}
