import { useStatus } from "../../lib/api";
import { useWsState } from "../../lib/useQuote";
import { HeaderMenu } from "./HeaderMenu";
import { LivenessChips } from "./LivenessChips";

export function StatusHeader() {
  const { data, isError } = useStatus();
  const ws = useWsState();
  // The WS heartbeat is the fresher signal when the socket is open.
  const marketData = ws.socket === "open" ? ws.marketData : data?.marketData;

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
            <span className={`chip chip-${marketData}`} title={`dxlink: ${ws.dxlink}`}>
              {marketData === "live" ? "● live" : marketData === "cached" ? "◐ cached" : "○ disconnected"}
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
