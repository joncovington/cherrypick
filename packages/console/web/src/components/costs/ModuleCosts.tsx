import type { TradingMode } from "@console/shared";
import { useModuleCosts } from "../../lib/api";
import { matchesArm } from "../../lib/armTag";
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
export function ModuleCosts({
  module,
  mode,
  era,
  arm = null,
}: {
  module: "meic" | "flies";
  mode: TradingMode;
  era: string | null;
  /** The page's arm picker: narrows every row to that arm; null is every arm. */
  arm?: string | null;
}) {
  const costs = useModuleCosts(module, mode, era);
  // The page's arm picker, applied to what was read: the totals CostsView sums are that arm's.
  const picked = <T extends { arm: string }>(rows: T[] | undefined): T[] =>
    (rows ?? []).filter((r) => matchesArm(r.arm, arm));
  return (
    <div className="cards cards-wide">
      <CostsView
        arms={picked(costs.data?.arms)}
        slippageInGross={costs.data?.slippageInGross ?? true}
        since={costs.data?.since ?? null}
        sinceLabel="the era's start"
        sinceRows={picked(costs.data?.entryOutcomes)}
        allRows={picked(costs.data?.entryOutcomesAll)}
        loading={costs.isLoading}
        premiumNote={NOTES[module].premium}
        gateNote={NOTES[module].gates}
        updatedAt={costs.dataUpdatedAt}
      />
    </div>
  );
}
