import { useStatus } from "../../lib/api";
import { useWsState } from "../../lib/useQuote";

function ageLabel(ageSeconds: number | null): string {
  if (ageSeconds === null) return "—";
  if (ageSeconds < 90) return `${Math.round(ageSeconds)}s`;
  if (ageSeconds < 5400) return `${Math.round(ageSeconds / 60)}m`;
  return `${(ageSeconds / 3600).toFixed(1)}h`;
}

export function StatusHeader() {
  const { data, isError } = useStatus();
  const ws = useWsState();
  // The WS heartbeat is the fresher signal when the socket is open.
  const marketData = ws.socket === "open" ? ws.marketData : data?.marketData;

  return (
    <header className="status-header">
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
            {data.sources.map((s) => (
              <span
                key={s.key}
                className={`chip ${s.present ? "chip-ok" : "chip-missing"}`}
                title={s.present ? `last write ${ageLabel(s.ageSeconds)} ago` : "not found"}
              >
                {s.label} {ageLabel(s.ageSeconds)}
              </span>
            ))}
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
