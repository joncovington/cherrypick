import { useState } from "react";
import { LiveQuoteRow } from "../../components/LiveQuote";
import { SystemCard, useSystem } from "./SuiteCards";
import { LogsCard } from "./EquityCard";
import { useWsState } from "../../lib/useQuote";
import { useOverview, useMorningReport } from "../../lib/api";

const WATCH_SYMBOLS = ["SPX", "XSP", "QQQ", "IWM"];

type Segment = "quotes" | "system" | "logs" | null;

function phaseChipClass(phase: string): string {
  if (phase === "green") return "chip-ok";
  if (phase === "yellow") return "chip-warn";
  if (phase === "red") return "chip-missing";
  return "chip";
}

/**
 * The one-line status bar: watchdog/session/morning-phase/halt state, live quotes, module/service
 * counts, the last WARN. The quotes/system/logs segments open a drawer with the full table they
 * summarise. All of this used to sit in permanent cards or the page-title row; the no-scroll
 * redesign (2026-09) demoted it here so the page fits 1440×900 without losing anything, a click
 * away instead of a scroll (or a title row) away.
 */
export function StatusBar() {
  const [open, setOpen] = useState<Segment>(null);
  const ws = useWsState();
  const { data: system } = useSystem();
  const { data: overview } = useOverview();
  const wd = overview?.watchdog;
  const { data: morning } = useMorningReport();
  const phase = morning?.current?.phase ?? null;

  const toggle = (seg: Segment) => setOpen((cur) => (cur === seg ? null : seg));

  return (
    <>
      <div className="statusbar">
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
        {phase && (
          <span className={`chip ${phaseChipClass(phase.phase)}`}>
            morning {phase.phase.toUpperCase()}
            {phase.gatesMeasured !== null && phase.gatesTotal !== null &&
              ` · ${String(phase.gatesMet ?? 0)} of ${String(phase.gatesTotal)} measured gates met`}
          </span>
        )}
        {system && (
          <span className={`chip ${system.halted.active ? "chip-missing" : "chip-ok"}`}>
            {system.halted.active ? "LIVE HALTED" : "halt flag clear"}
          </span>
        )}
        <span className="statusbar-sep" />
        <button type="button" className="statusbar-seg" onClick={() => toggle("quotes")}>
          {WATCH_SYMBOLS.map((s) => `${s} ${ws.marketData === "live" ? "●" : "◐"}`).join(" · ")}
        </button>
        <span className="statusbar-sep" />
        <button type="button" className="statusbar-seg" onClick={() => toggle("system")}>
          {system !== undefined
            ? `${String(system.modules.length)} modules · ${String(system.services.length)} services`
            : "system"}
        </button>
        <span className="statusbar-sep" />
        <button type="button" className="statusbar-seg" onClick={() => toggle("logs")}>
          recent logs
        </button>
        <span className="statusbar-hint muted">click a segment for the full table</span>
      </div>
      {open !== null && (
        <div className="statusbar-drawer">
          {open === "quotes" && (
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
          )}
          {open === "system" && <SystemCard />}
          {open === "logs" && <LogsCard />}
        </div>
      )}
    </>
  );
}
