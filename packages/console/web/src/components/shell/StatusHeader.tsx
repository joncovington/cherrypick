import { useOverview, useStatus } from "../../lib/api";
import { FuturesTicker } from "./FuturesTicker";
import { togglePalette } from "./paletteStore";

/**
 * The top bar, on every page: the futures ticker and the session chip with the clock (the session
 * is the watchdog's own verdict, read off the overview payload). The health chips that used to sit
 * here (DXLink, credential scope, producer liveness) moved to the bottom `StatusBar` on
 * 2026-10-01, so this bar carries the market and that one carries the suite.
 *
 * Since 2026-10-12 it carries NO brand: the brand block moved into the rail's head — the rail
 * spans the full window height, so its right border passes the wordmark without a notch, which a
 * brand cell here could never promise (it would have had to track the rail's two column widths).
 * This bar is context for the CONTENT: ticker, session, clock, and the command-palette trigger.
 */
export function StatusHeader() {
  const { data } = useStatus();
  const wd = useOverview().data?.watchdog;
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
        <div className="status-clock">
          {data ? (
            <span title="Eastern time">{data.nowEt} ET</span>
          ) : (
            <span className="skeleton skeleton-text" style={{ width: "11rem" }} />
          )}
        </div>
        <button
          type="button"
          className="cmdk-trigger"
          title="command palette (Ctrl+K)"
          aria-label="open the command palette"
          onClick={() => togglePalette()}
        >
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
            <circle cx="7" cy="7" r="4.5" />
            <line x1="10.5" y1="10.5" x2="14" y2="14" />
          </svg>
          <span className="cmdk-trigger-kbd" aria-hidden="true">⌘K</span>
        </button>
      </div>
    </header>
  );
}
