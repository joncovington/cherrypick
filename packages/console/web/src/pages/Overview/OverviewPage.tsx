import { useOverview } from "../../lib/api";
import { LiveQuoteRow } from "../../components/LiveQuote";
import type { WatchdogFinding } from "@console/shared";

const WATCH_SYMBOLS = ["SPX", "XSP", "QQQ", "IWM"];

function statusClass(status: string): string {
  const s = status.toUpperCase();
  if (s === "OK") return "status-ok";
  if (s === "WARN" || s === "WARNING") return "status-warn";
  return "status-err";
}

function FindingRow({ f }: { f: WatchdogFinding }) {
  return (
    <tr>
      <td>
        <span className={`dot ${statusClass(f.status)}`} />
      </td>
      <td>{f.title}</td>
      <td className="muted">{f.message}</td>
    </tr>
  );
}

function SkeletonRows({ n }: { n: number }) {
  return (
    <>
      {Array.from({ length: n }, (_, i) => (
        <tr key={i}>
          <td colSpan={3}>
            <span className="skeleton skeleton-text" style={{ width: `${55 + ((i * 17) % 35)}%` }} />
          </td>
        </tr>
      ))}
    </>
  );
}

export function OverviewPage() {
  const { data, isError, dataUpdatedAt } = useOverview();
  const wd = data?.watchdog;

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Overview</h1>
        {wd?.overall && (
          <span className={`chip ${wd.overall === "OK" ? "chip-ok" : "chip-warn"}`}>
            watchdog {wd.overall}
            {wd.ageSeconds !== null && ` · ${Math.round(wd.ageSeconds / 60)}m ago`}
          </span>
        )}
        {wd && (
          <span className="chip">
            {wd.isTradingDay ? (wd.inSession ? "market open" : "trading day, closed") : "non-trading day"}
          </span>
        )}
      </div>

      <div className="cards">
        <section className="card">
          <h2>Live quotes</h2>
          <table className="data-table">
            <thead>
              <tr>
                <th>sym</th>
                <th>last</th>
                <th>bid</th>
                <th>ask</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {WATCH_SYMBOLS.map((s) => (
                <LiveQuoteRow key={s} symbol={s} />
              ))}
            </tbody>
          </table>
        </section>

        <section className={`card ${isError ? "card-stale" : ""}`}>
          <h2>Watchdog findings</h2>
          <table className="data-table">
            <tbody>
              {wd ? wd.findings.map((f) => <FindingRow key={f.key} f={f} />) : <SkeletonRows n={8} />}
            </tbody>
          </table>
          {isError && (
            <div className="stale-note">
              stale since {new Date(dataUpdatedAt).toLocaleTimeString()} — console API unreachable
            </div>
          )}
        </section>

        <section className="card">
          <h2>Managed services</h2>
          <table className="data-table">
            <tbody>
              {data ? (
                data.services.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <span className={`dot ${s.enabled ? "status-ok" : "status-off"}`} />
                    </td>
                    <td>{s.id}</td>
                    <td className="muted">{s.enabled ? "enabled" : "disabled"}</td>
                  </tr>
                ))
              ) : (
                <SkeletonRows n={2} />
              )}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}
