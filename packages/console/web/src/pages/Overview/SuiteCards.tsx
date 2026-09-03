import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, fmtMoney, fmtPct } from "../../components/DataTable";
import { CalendarHeatmap } from "../../components/CalendarHeatmap";
import { useReview } from "../../lib/api";
import { ModuleCellLink } from "../../components/ModuleLink";

interface SystemPanel {
  timezone: string | null;
  modules: Array<{ id: string; enabled: boolean; kind: string | null; streamer: boolean | null; liveTrading: boolean | null }>;
  services: Array<{
    id: string;
    enabled: boolean;
    autoRestart: boolean;
    launched: string | null;
    pid: number | null;
    health: string | null;
    note: string | null;
    detail: string | null;
  }>;
  watchdog: { intervalMinutes: number | null; renotifyMinutes: number | null; drawdownGuard: boolean | null };
  notify: { channels: string[]; tradeChannels: string[]; webhookStatus: string | null };
  halted: { active: boolean; path: string };
}

/** Shared with the page-title row's halt/live chips, so both read one fetch of the same data. */
export function useSystem() {
  return useQuery<SystemPanel>({
    queryKey: ["system"],
    queryFn: async () => {
      const res = await fetch("/api/system");
      if (!res.ok) throw new Error(`system: HTTP ${res.status}`);
      return (await res.json()) as SystemPanel;
    },
    refetchInterval: 60_000,
  });
}

export function SystemCard() {
  const { data, isLoading, dataUpdatedAt } = useSystem();

  return (
    <Card title="System" collapseKey="system" updatedAt={dataUpdatedAt}>
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "50%" }} />
      ) : (
        <>
          <div style={{ marginBottom: "0.6rem" }}>
            <span className={`chip ${data?.halted.active === true ? "chip-missing" : "chip-ok"}`}>
              {data?.halted.active === true ? "LIVE HALTED (flag present)" : "halt flag clear"}
            </span>
            {data?.timezone !== null && <span className="chip">{data?.timezone}</span>}
            {data?.notify.channels.map((c) => (
              <span key={c} className="chip">notify: {c}</span>
            ))}
          </div>
          <div className="table-scroll">
            <table className="data-table num-from-1">
              <thead>
                <tr><th>module</th><th>enabled</th><th>kind</th><th>streamer</th><th>live trading</th></tr>
              </thead>
              <tbody>
                {data?.modules.map((m) => (
                  <tr key={m.id}>
                    <td><ModuleCellLink id={m.id}>{m.id}</ModuleCellLink></td>
                    <td>{m.enabled ? "yes" : "no"}</td>
                    <td className="muted">{m.kind ?? "—"}</td>
                    <td className="muted">{m.streamer === null ? "—" : m.streamer ? "on" : "off"}</td>
                    <td className={m.liveTrading === true ? "pnl-neg" : "muted"}>
                      {m.liveTrading === null ? "—" : m.liveTrading ? "ENABLED" : "paper only"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="table-scroll" style={{ marginTop: "0.6rem" }}>
            <table className="data-table num-from-1">
              <thead>
                <tr><th>service</th><th>status</th><th>enabled</th><th>auto-restart</th><th>launched</th><th>pid</th></tr>
              </thead>
              <tbody>
                {data?.services.map((s) => (
                  <tr key={s.id}>
                    <td>{s.id}</td>
                    {/* The watchdog's own verdict from its last tick — the console renders it,
                        never re-derives it. Hover shows the finding's full message. */}
                    <td className={s.health === "OK" ? "pnl-pos" : s.health === null ? "muted" : "pnl-neg"} title={s.detail ?? undefined}>
                      {s.note ?? "no watchdog report"}
                    </td>
                    <td>{s.enabled ? "yes" : "no"}</td>
                    <td className="muted">{s.autoRestart ? "yes" : "no"}</td>
                    <td className="muted">{s.launched?.slice(0, 16).replace("T", " ") ?? "—"}</td>
                    <td className="muted">{s.pid ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ fontSize: 11, marginBottom: 0 }}>
            watchdog every {data?.watchdog.intervalMinutes ?? "—"}m · renotify {data?.watchdog.renotifyMinutes ?? "—"}m
            {data?.watchdog.drawdownGuard !== null && ` · drawdown guard ${data?.watchdog.drawdownGuard === true ? "on" : "off"}`}
          </p>
        </>
      )}
    </Card>
  );
}

/**
 * Suite consistency at a glance: one cell per session, the whole era on one strip. The EOD card
 * below answers for ONE session and cannot show this — a run of red days beside a green total is
 * exactly what a single-session view hides, and it is the view every trading journal leads with.
 *
 * Fed from the review fact sets (`era.suiteDaily`), not a fresh pass over any ledger, so it cannot
 * disagree with the Review page. A session whose modules were all unreadable is absent from the
 * series and renders as a gap, never as a flat zero day.
 */
export function SuiteCalendarCard() {
  const { data, isLoading, dataUpdatedAt } = useReview();
  const days = data?.era.suiteDaily ?? [];
  const from = data?.era.from ?? null;
  const to = data?.era.to ?? null;
  return (
    <Card
      title="Suite net by session"
      collapseKey="suite-calendar"
      updatedAt={dataUpdatedAt}
      controls={
        from !== null ? (
          <span className="chip">
            {from} → {to} · {days.length} sessions
          </span>
        ) : undefined
      }
    >
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "40%" }} />
      ) : (
        <>
          <CalendarHeatmap
            days={days.map((d) => ({ date: d.session, net: d.net, count: d.closed }))}
            countLabel="closed"
          />
          <p className="muted" style={{ fontSize: 11, marginBottom: 0, marginTop: "0.4rem" }}>
            Every readable module summed per session, from the review fact sets. Deliberately a
            shape, not a total: these books differ in scale by more than an order of magnitude, so
            read the pattern of days rather than the size of any one cell.
          </p>
        </>
      )}
    </Card>
  );
}

interface Eod {
  session: string | null;
  isLastSession: boolean;
  suite: { net: number; trades: number; wins: number; losses: number };
  byModule: Array<{ module: string; net: number }>;
  reports: Array<{ module: string; kind: string; file: string; exists: boolean }>;
}

export function EodCard() {
  const [openReport, setOpenReport] = useState<string | null>(null);
  const { data, isLoading, dataUpdatedAt } = useQuery<Eod>({
    queryKey: ["eod"],
    queryFn: async () => {
      const res = await fetch("/api/eod");
      if (!res.ok) throw new Error(`eod: HTTP ${res.status}`);
      return (await res.json()) as Eod;
    },
    refetchInterval: 120_000,
  });
  const report = useQuery<{ html: string }>({
    queryKey: ["eod-report", openReport],
    queryFn: async () => {
      const res = await fetch(`/api/eod/report?file=${encodeURIComponent(openReport!)}`);
      if (!res.ok) throw new Error("report unavailable");
      return (await res.json()) as { html: string };
    },
    enabled: openReport !== null,
  });

  const wins = data?.suite.wins ?? 0;
  const losses = data?.suite.losses ?? 0;
  return (
    <Card
      title={`End of day${data?.session != null ? ` — ${data.session}${data.isLastSession ? " · last session" : ""}` : ""}`}
      collapseKey="eod"
      updatedAt={dataUpdatedAt}
    >
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "40%" }} />
      ) : (
        <>
          <div className="stats-grid">
            <div className="stat-tile">
              <span className="stat-label">session net</span>
              <span className={`stat-value ${(data?.suite.net ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}`}>{fmtMoney(data?.suite.net ?? 0)}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">trades (this era)</span>
              <span className="stat-value">{data?.suite.trades ?? "—"}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">win rate</span>
              <span className="stat-value">{fmtPct(wins + losses > 0 ? (wins / (wins + losses)) * 100 : null)}</span>
            </div>
          </div>
          <table className="data-table num-from-1" style={{ marginTop: "0.6rem" }}>
            <thead><tr><th>module</th><th>session net</th></tr></thead>
            <tbody>
              {data?.byModule.map((m) => (
                <tr key={m.module}>
                  <td><ModuleCellLink id={m.module}>{m.module}</ModuleCellLink></td>
                  <td className={m.net >= 0 ? "pnl-pos" : "pnl-neg"}>{fmtMoney(m.net)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", marginTop: "0.6rem" }}>
            {data?.reports.filter((r) => r.exists).map((r) => (
              <button
                key={r.file}
                type="button"
                className="btn"
                onClick={() => setOpenReport(openReport === r.file ? null : r.file)}
              >
                {r.module} {r.kind} {openReport === r.file ? "▾" : "↗"}
              </button>
            ))}
            {data?.reports.every((r) => !r.exists) === true && <span className="muted">no report files yet</span>}
          </div>
          {openReport !== null && (
            <div className="report-body">
              {report.isLoading ? (
                <span className="skeleton skeleton-text" style={{ width: "60%" }} />
              ) : report.data !== undefined ? (
                <div dangerouslySetInnerHTML={{ __html: report.data.html }} />
              ) : (
                <p className="muted">report unavailable</p>
              )}
            </div>
          )}
        </>
      )}
    </Card>
  );
}
