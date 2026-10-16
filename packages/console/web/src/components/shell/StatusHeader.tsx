import { useOverview, useStatus } from "../../lib/api";
import { setViewMode, useViewMode } from "../../lib/viewMode";
import { FuturesTicker } from "./FuturesTicker";

/**
 * The top bar, on every page: the futures ticker, the session chip with the clock (the session
 * is the watchdog's own verdict, read off the overview payload), and the simple/detail view
 * switch. The health chips that used to sit here (DXLink, credential scope, producer liveness)
 * moved to the bottom `StatusBar` on 2026-10-01, so this bar carries the market and that one
 * carries the suite.
 *
 * Since 2026-10-12 it carries NO brand: the brand block moved into the rail's head — the rail
 * spans the full window height, so its right border passes the wordmark without a notch, which a
 * brand cell here could never promise (it would have had to track the rail's two column widths).
 * This bar is context for the CONTENT: ticker, session, clock and view.
 */
export function StatusHeader() {
  const { data } = useStatus();
  const wd = useOverview().data?.watchdog;
  const view = useViewMode();
  return (
    <header className="status-header">
      <FuturesTicker />
      <div className="status-right">
        {/* No pill until the watchdog has reported once: an absent state file reads as "not a
            trading day", which is what a fresh install showed on a weekday for its first minutes. */}
        {wd && wd.ts !== null && (
          <span className={`chip ${wd.inSession ? "chip-ok" : ""}`}>
            {wd.isTradingDay ? (wd.inSession ? "market open" : "trading day, closed") : "non-trading day"}
          </span>
        )}
        <div className="mode-toggle view-toggle" role="group" aria-label="view">
          <button
            type="button"
            className={`mode-btn ${view === "simple" ? "active" : ""}`}
            aria-pressed={view === "simple"}
            title="each module's summary, main chart, performance and positions"
            onClick={() => setViewMode("simple")}
          >
            simple
          </button>
          <button
            type="button"
            className={`mode-btn ${view === "detail" ? "active" : ""}`}
            aria-pressed={view === "detail"}
            title="every tab: the tables, gates, regimes and study slides too"
            onClick={() => setViewMode("detail")}
          >
            detail
          </button>
        </div>
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
