import { useFeatures } from "./useFeatures";
import { isBacktestResultsOn } from "./visibility";

/**
 * Whether results derived from the vendor's licensed data may be shown here (`features.backtest_results`,
 * failing closed: false while loading and on a failed read). Every hook that reads such a result asks
 * this before it fetches, so a closed gate asks the server nothing; the server refuses on its own too.
 */
export function useBacktestResultsOn(): boolean {
  return isBacktestResultsOn(useFeatures());
}

/** What a panel shows in place of figures it may not show. */
export const WITHHELD_TEXT =
  "Withheld: these figures come from the vendor's licensed data, shown only while it is on this machine and before the licence's purge date.";

export function Withheld() {
  return <p className="muted">{WITHHELD_TEXT}</p>;
}
