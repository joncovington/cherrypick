import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { MarkedEquitySection } from "../../components/nav/MarkedEquitySection";
import { CostsView } from "../../components/costs/CostsView";
import { usePmccCosts, usePmccEquity } from "../../lib/api";

/**
 * pmcc's performance page. It leads with the daily MARKED equity (`pmcc equity`): every position at
 * each session's close by the tracker's own valuation -- the long at its mark, every short sold
 * (realised once rolled), any delivered shares. A held long can sit far below its cost for months
 * while every weekly short closes a small win; the closed-trade view below sees only the wins until
 * the long itself is sold. Scoped to the page's era, like the arm comparison.
 */
export function PmccPerformance({ era }: { era: string | null }) {
  const equity = usePmccEquity(era);
  return (
    <div className="cards cards-wide">
      <MarkedEquitySection
        equity={equity.data}
        loading={equity.isLoading}
        collapsePrefix="pmcc-perf"
        note="Dollars from zero, net of costs to date: each closed position's net on its close session, each open one valued at the session's close by the tracker (the long at its mark, each short realised or marked, delivered shares at spot). A session where a position cannot be priced carries its last value and is counted."
      />
      <PerformanceSlide module="pmcc" />
    </div>
  );
}

/** pmcc's costs page: what the shorts' premium paid for, and how often an entry got through. */
export function PmccCosts({ era }: { era: string | null }) {
  const costs = usePmccCosts(era);
  return (
    <div className="cards cards-wide">
      <CostsView
        arms={costs.data?.arms ?? []}
        since={costs.data?.since ?? null}
        sinceLabel="the era's first entry"
        sinceRows={costs.data?.entryOutcomes ?? []}
        allRows={costs.data?.entryOutcomesAll ?? []}
        loading={costs.isLoading}
        premiumNote="every short sold, rolls included (entry mid x 100 x qty)"
        gateNote="entry_pacing is the design (one symbol enters per session), and an ex-dividend or earnings span is a refusal the module makes on purpose: early assignment is measured, never modelled."
        updatedAt={costs.dataUpdatedAt}
      />
    </div>
  );
}
