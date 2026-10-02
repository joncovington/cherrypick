import { useOverview, useStatus } from "../../lib/api";
import { HeaderMenu } from "./HeaderMenu";
import { FuturesTicker } from "./FuturesTicker";

/**
 * The top bar, on every page: the menu, the futures ticker beside it, and the session chip with the
 * clock (the session is the watchdog's own verdict, read off the overview payload). The health
 * chips that used to sit here (DXLink, credential scope, producer liveness) moved to the bottom
 * `StatusBar` on 2026-10-01, so this bar carries the market and that one carries the suite.
 */
export function StatusHeader() {
  const { data } = useStatus();
  const wd = useOverview().data?.watchdog;
  return (
    <header className="status-header">
      <HeaderMenu />
      <FuturesTicker />
      <div className="status-right">
        {/* No pill until the watchdog has reported once: an absent state file reads as "not a
            trading day", which is what a fresh install showed on a weekday for its first minutes. */}
        {wd && wd.ts !== null && (
          <span className={`chip ${wd.inSession ? "chip-ok" : ""}`}>
            {wd.isTradingDay ? (wd.inSession ? "market open" : "trading day, closed") : "non-trading day"}
          </span>
        )}
        <div className="status-clock">
          {data ? (
            <span title="Eastern time">{data.nowEt} ET</span>
          ) : (
            <span className="skeleton skeleton-text" style={{ width: "11rem" }} />
          )}
        </div>
      </div>
    </header>
  );
}
