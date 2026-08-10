import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, fmtMoney, fmtPct } from "../../components/DataTable";

interface Check {
  value: number | null;
  threshold: number | string;
  pass: boolean;
}

interface Reading {
  sample: number;
  winRate: number | null;
  days: number;
  netPnl: number;
  netPnl2xSlippage: number;
  slippageCoverage: number;
  returnOnCapital: number | null;
  capitalCoverage: number;
  sharpe: number | null;
  maxDrawdown: number;
  sampleProgress: { n: number; nextTarget: number | null; progress: number };
}

interface Calibration {
  modules: Array<{
    module: string;
    champion: string | null;
    tags: Array<{ tag: string; reading: Reading; qualification: { qualified: boolean; checks: Record<string, Check> }; role: string }>;
    verdict: { eligible: boolean; recommendation: string; reason: string } | null;
  }>;
}

const CHECK_LABEL: Record<string, string> = { sample: "sample", win_rate: "win rate", days: "days" };

/** A qualification check as a progress bar — value against its threshold. */
function CheckBar({ name, c }: { name: string; c: Check }) {
  const pct = c.value !== null && typeof c.threshold === "number" ? Math.min(1, c.value / c.threshold) * 100 : 0;
  const shown =
    c.value === null
      ? "—"
      : name === "win_rate"
        ? `${(c.value * 100).toFixed(0)}% / ${(Number(c.threshold) * 100).toFixed(0)}%`
        : `${c.value} / ${c.threshold}`;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.1rem 0" }}>
      <span className="muted" style={{ width: "4.5rem", fontSize: 11 }}>{CHECK_LABEL[name] ?? name}</span>
      <div style={{ flex: 1, height: 6, background: "var(--row-line)", borderRadius: 3 }}>
        <div style={{ width: `${pct}%`, height: "100%", background: c.pass ? "var(--ok)" : "var(--warn)", borderRadius: 3 }} />
      </div>
      <span style={{ width: "5.5rem", textAlign: "right", fontSize: 11, fontFamily: "var(--num-face)" }}>{shown}</span>
    </div>
  );
}

function roleClass(role: string): string {
  if (role === "champion") return "chain-badge-long";
  if (role === "beats champion") return "chain-badge-long";
  if (role === "qualified") return "chain-badge-long";
  return "";
}

export function ChampionsCard() {
  const { data, isLoading, dataUpdatedAt } = useQuery<Calibration>({
    queryKey: ["calibration"],
    queryFn: async () => {
      const res = await fetch("/api/calibration");
      if (!res.ok) throw new Error(`calibration: HTTP ${res.status}`);
      return (await res.json()) as Calibration;
    },
    refetchInterval: 120_000,
  });

  return (
    <Card title="Champions & challengers" updatedAt={dataUpdatedAt}>
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "50%" }} />
      ) : (
        (data?.modules ?? []).map((m) => (
          <div key={m.module} style={{ marginBottom: "1rem" }}>
            <h2 style={{ marginBottom: "0.35rem" }}>
              {m.module}
              {m.champion !== null && <span className="chip" style={{ marginLeft: "0.5rem" }}>champion: {m.champion}</span>}
              {m.champion === null && <span className="chip" style={{ marginLeft: "0.5rem" }}>parallel arms — qualified independently</span>}
            </h2>
            {m.verdict !== null && (
              <p className={m.verdict.eligible ? "pnl-pos" : "muted"} style={{ fontSize: 12, margin: "0 0 0.4rem" }}>
                {m.verdict.reason}
              </p>
            )}
            <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(19rem, 1fr))", gap: "0.6rem" }}>
              {m.tags.slice(0, 8).map((t) => (
                <div key={t.tag} style={{ border: "1px solid var(--border)", borderRadius: 4, padding: "0.5rem 0.65rem" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.3rem" }}>
                    <strong style={{ fontSize: 12.5 }}>{t.tag}</strong>
                    <span className={`chain-badge ${roleClass(t.role)}`}>{t.role}</span>
                    <span className={`${t.reading.netPnl >= 0 ? "pnl-pos" : "pnl-neg"}`} style={{ marginLeft: "auto", fontFamily: "var(--num-face)", fontSize: 12 }}>
                      {fmtMoney(t.reading.netPnl)}
                    </span>
                  </div>
                  {Object.entries(t.qualification.checks).map(([name, c]) => (
                    <CheckBar key={name} name={name} c={c} />
                  ))}
                  <div className="muted" style={{ fontSize: 10.5, marginTop: "0.25rem" }}>
                    RoC {t.reading.returnOnCapital !== null ? `${(t.reading.returnOnCapital * 100).toFixed(1)}%` : "—"}
                    {" · "}Sharpe {t.reading.sharpe !== null ? t.reading.sharpe.toFixed(2) : "—"}
                    {" · "}max DD {fmtMoney(t.reading.maxDrawdown)}
                    {t.reading.slippageCoverage > 0 && ` · 2× slip ${fmtMoney(t.reading.netPnl2xSlippage)}`}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))
      )}
    </Card>
  );
}

interface SystemPanel {
  timezone: string | null;
  modules: Array<{ id: string; enabled: boolean; kind: string | null; streamer: boolean | null; champion: string | null; liveTrading: boolean | null }>;
  services: Array<{ id: string; enabled: boolean; autoRestart: boolean; launched: string | null; pid: number | null }>;
  watchdog: { intervalMinutes: number | null; renotifyMinutes: number | null; drawdownGuard: boolean | null };
  notify: { channels: string[]; tradeChannels: string[]; webhookStatus: string | null };
  halted: { active: boolean; path: string };
}

export function SystemCard() {
  const { data, isLoading, dataUpdatedAt } = useQuery<SystemPanel>({
    queryKey: ["system"],
    queryFn: async () => {
      const res = await fetch("/api/system");
      if (!res.ok) throw new Error(`system: HTTP ${res.status}`);
      return (await res.json()) as SystemPanel;
    },
    refetchInterval: 60_000,
  });

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
                <tr><th>module</th><th>enabled</th><th>kind</th><th>streamer</th><th>champion</th><th>live trading</th></tr>
              </thead>
              <tbody>
                {data?.modules.map((m) => (
                  <tr key={m.id}>
                    <td>{m.id}</td>
                    <td>{m.enabled ? "yes" : "no"}</td>
                    <td className="muted">{m.kind ?? "—"}</td>
                    <td className="muted">{m.streamer === null ? "—" : m.streamer ? "on" : "off"}</td>
                    <td className="muted">{m.champion ?? "—"}</td>
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
                <tr><th>service</th><th>enabled</th><th>auto-restart</th><th>launched</th><th>pid</th></tr>
              </thead>
              <tbody>
                {data?.services.map((s) => (
                  <tr key={s.id}>
                    <td>{s.id}</td>
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
              <span className="stat-label">trades (all-time)</span>
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
                  <td>{m.module}</td>
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
