import { useState } from "react";
import { LogsCard } from "../../pages/Overview/EquityCard";
import { useWsState } from "../../lib/useQuote";
import { useOverview, useMorningReport, useStatus } from "../../lib/api";
import { LivenessChips } from "./LivenessChips";

const DXLINK_LABEL: Record<string, string> = {
  connected: "● dxlink connected",
  connecting: "dxlink connecting…",
  disconnected: "○ dxlink idle",
  error: "⚠ dxlink error",
};

type Segment = "logs" | null;

function phaseChipClass(phase: string): string {
  if (phase === "green") return "chip-ok";
  if (phase === "yellow") return "chip-warn";
  if (phase === "red") return "chip-missing";
  return "chip";
}

/**
 * The one-line status bar along the bottom of every page: the suite's health first (the console's
 * DXLink session, a read-only credential, each producer's liveness against its own cadence), then
 * watchdog/session/morning-phase state and the logs, which open in a drawer. (The SPX/XSP/QQQ/IWM
 * quotes and the module/service counts went on 2026-10-01; the top bar's futures ticker carries
 * the market now.)
 *
 * It began as the Overview's own bar in the no-scroll redesign (2026-09); since 2026-10-01 it is
 * the shell's, on every page, and the health chips moved down into it from the header so the top
 * bar could carry the menu and the futures ticker. The halt flag is not here: it sits on the
 * Overview beside the LIVE chip, the two facts about live trading read together.
 */
export function StatusBar() {
  const [open, setOpen] = useState<Segment>(null);
  const ws = useWsState();
  const { data: status, isError: statusError } = useStatus();
  // The WS heartbeat is the fresher signal when the socket is open.
  const dxlink = ws.socket === "open" ? ws.dxlink : (status?.dxlink ?? "disconnected");
  const { data: overview } = useOverview();
  const wd = overview?.watchdog;
  const { data: morning } = useMorningReport();
  const phase = morning?.current?.phase ?? null;

  const toggle = (seg: Segment) => setOpen((cur) => (cur === seg ? null : seg));

  return (
    <div className="statusbar-wrap">
      <div className="statusbar">
        {statusError && <span className="chip chip-missing">console API unreachable</span>}
        {status !== undefined && (
          <>
            {/* The console's OWN DXLink websocket -- separate from the shared stream cache's
                freshness (the streamer chip beside it). "idle" is the normal state when no
                browser client is watching live quotes; it connects lazily, ref-counted, and is
                not itself a fault the way "error" is. */}
            <span
              className={`chip ${dxlink === "connected" ? "chip-ok" : dxlink === "error" ? "chip-warn" : ""}`}
              title="The console opens its own DXLink session only while a page is watching live quotes. Otherwise it reads the shared stream cache (see the streamer chip) -- idle here does not mean the data is stale."
            >
              {DXLINK_LABEL[dxlink] ?? dxlink}
            </span>
            {status.credentialScope === "read" && (
              <span className="chip chip-warn" title="the suite credential's refresh token is read-only — broker dry-run validation is disabled; re-run credentials set with a trade-scoped token to enable it">
                read-only credential
              </span>
            )}
          </>
        )}
        <LivenessChips />
        <span className="statusbar-sep" />
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
        <span className="statusbar-sep" />
        <button type="button" className="statusbar-seg" onClick={() => toggle("logs")}>
          recent logs
        </button>
      </div>
      {open !== null && (
        <div className="statusbar-drawer">
          <LogsCard />
        </div>
      )}
    </div>
  );
}
