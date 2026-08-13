import { useOverview } from "../../lib/api";
import { LiveQuoteRow } from "../../components/LiveQuote";
import { EquityCard, LogsCard } from "./EquityCard";
import { SystemCard, EodCard, useSystem } from "./SuiteCards";
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
      <td className="muted" style={{ whiteSpace: "normal" }}>{f.message}</td>
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
  const { data: system } = useSystem();
  const liveCount = system?.modules.filter((m) => m.liveTrading === true).length ?? 0;

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
        {/* The single most safety-relevant fact on the page, promoted here so it's never below
            the fold -- SystemCard further down still carries the full module-by-module table. */}
        {system && (
          <span className={`chip ${system.halted.active ? "chip-missing" : "chip-ok"}`}>
            {system.halted.active ? "LIVE HALTED" : "halt flag clear"}
          </span>
        )}
        {liveCount > 0 && <span className="chip chip-missing">{liveCount} module{liveCount === 1 ? "" : "s"} LIVE</span>}
      </div>

      <div className="cards cards-wide" style={{ marginBottom: "0.75rem" }}>
        <EquityCard />
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

      <div className="cards cards-wide" style={{ marginTop: "0.75rem" }}>
        <EodCard />
        <SystemCard />
        <LogsCard />
      </div>
    </div>
  );
}
