import { useStatus } from "../../lib/api";
import { useWsState } from "../../lib/useQuote";
import { HeaderMenu } from "./HeaderMenu";
import { LivenessChips } from "./LivenessChips";

const DXLINK_LABEL: Record<string, string> = {
  connected: "● dxlink connected",
  connecting: "dxlink connecting…",
  disconnected: "○ dxlink idle",
  error: "⚠ dxlink error",
};

export function StatusHeader() {
  const { data, isError } = useStatus();
  const ws = useWsState();
  // The WS heartbeat is the fresher signal when the socket is open.
  const dxlink = ws.socket === "open" ? ws.dxlink : (data?.dxlink ?? "disconnected");

  return (
    <header className="status-header">
      <HeaderMenu />
      <div className="status-clock">
        {data ? (
          <span title="Eastern time">{data.nowEt} ET</span>
        ) : (
          <span className="skeleton skeleton-text" style={{ width: "11rem" }} />
        )}
      </div>
      <div className="status-items">
        {data ? (
          <>
            {/* The console's OWN DXLink websocket -- separate from the shared stream cache's
                freshness (that's LivenessChips' own "streamer" chip below). "idle" is the normal
                state when no browser client is watching live quotes; it connects lazily,
                ref-counted, and is not itself a fault the way "error" is. */}
            <span
              className={`chip ${dxlink === "connected" ? "chip-ok" : dxlink === "error" ? "chip-warn" : ""}`}
              title="The console opens its own DXLink session only while a page is watching live quotes. Otherwise it reads the shared stream cache (see the streamer chip) -- idle here does not mean the data is stale."
            >
              {DXLINK_LABEL[dxlink] ?? dxlink}
            </span>
            {data.credentialScope === "read" && (
              <span className="chip chip-warn" title="the suite credential's refresh token is read-only — broker dry-run validation is disabled; re-run credentials set with a trade-scoped token to enable it">
                read-only credential
              </span>
            )}
            <LivenessChips />
          </>
        ) : isError ? (
          <span className="chip chip-missing">console API unreachable</span>
        ) : (
          <>
            <span className="skeleton skeleton-chip" />
            <span className="skeleton skeleton-chip" />
            <span className="skeleton skeleton-chip" />
          </>
        )}
      </div>
    </header>
  );
}
