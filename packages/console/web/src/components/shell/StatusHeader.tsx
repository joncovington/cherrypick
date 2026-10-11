import { useOverview, useStatus } from "../../lib/api";
import { FuturesTicker } from "./FuturesTicker";
import { togglePalette } from "./paletteStore";

/**
 * The top bar, on every page: the brand block, the futures ticker beside it, and the session chip
 * with the clock (the session is the watchdog's own verdict, read off the overview payload). The
 * health chips that used to sit here (DXLink, credential scope, producer liveness) moved to the
 * bottom `StatusBar` on 2026-10-01, so this bar carries the market and that one carries the suite.
 *
 * Since 2026-10-11 it carries NO menu: the left rail is the one nav on every page (it renders in
 * the shell, Overview included), and the brand block is a static link home, not a dropdown
 * trigger. The shortcut legend the menu used to print moved to the command palette (the ⌘K
 * button beside the clock opens it, as does the chord itself).
 */
export function StatusHeader() {
  const { data } = useStatus();
  const wd = useOverview().data?.watchdog;
  return (
    <header className="status-header">
      {/* Static brand — a link home with a subtle indicator, never a menu trigger. The cherry
          stays on the wordmark: it is one of the three cherry roles (brand, LIVE, alert). */}
      <a className="brand-block" href="/" aria-label="cherrypick — home">
        <span className="brand-mark" aria-hidden="true" />
        <span className="brand-name">cherrypick</span>
      </a>
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
