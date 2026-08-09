import { useQuery } from "@tanstack/react-query";
import { useMeic } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";
import type { TradingMode } from "@console/shared";
import { MeicDeepCards } from "./MeicDeepCards";

interface MeicAnalytics {
  periods: Array<{ label: string; net: number; trades: number; wins: number; losses: number }>;
  exitReasons: Array<{ reason: string; count: number }>;
  feeDrag: { grossCredit: number; fees: number; netPnl: number; dragPct: number | null };
}

function useMeicAnalytics(mode: TradingMode) {
  return useQuery<MeicAnalytics>({
    queryKey: ["meic-analytics", mode],
    queryFn: async () => {
      const res = await fetch(`/api/meic/analytics?mode=${mode}`);
      if (!res.ok) throw new Error(`meic analytics: HTTP ${res.status}`);
      return (await res.json()) as MeicAnalytics;
    },
    refetchInterval: 30_000,
  });
}

export function MeicPage() {
  const [mode, setMode] = useMode();
  const { data, isLoading, isError } = useMeic(mode);
  const analytics = useMeicAnalytics(mode);
  const a = analytics.data;
  const totalExits = a?.exitReasons.reduce((s, r) => s + r.count, 0) ?? 0;

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>MEIC</h1>
        <PaperLiveBadge mode={mode} />
        <ModeToggle mode={mode} onChange={setMode} />
      </div>

      <div className="cards cards-wide">
        <section className="card">
          <h2>Performance (net = gross P&L; win = P&L − fees &gt; 0)</h2>
          <div className="stats-grid">
            {(a?.periods ?? []).map((p) => (
              <div key={p.label} className="stat-tile">
                <span className="stat-label">{p.label}</span>
                <span className={`stat-value ${p.net >= 0 ? "pnl-pos" : "pnl-neg"}`}>{fmtMoney(p.net)}</span>
                <span className="muted" style={{ fontSize: 11 }}>
                  {p.trades} trades · {p.wins}W/{p.losses}L
                  {p.wins + p.losses > 0 ? ` · ${((p.wins / (p.wins + p.losses)) * 100).toFixed(0)}%` : ""}
                </span>
              </div>
            ))}
          </div>
        </section>

        <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(18rem, 1fr))" }}>
          <DataCard
            title="Exit reasons"
            headers={["reason", "count", "%"]}
            loading={analytics.isLoading}
            rowCount={a?.exitReasons.length ?? 0}
          >
            {a?.exitReasons.map((r) => (
              <tr key={r.reason}>
                <td>{r.reason}</td>
                <td>{r.count}</td>
                <td className="muted">{totalExits > 0 ? `${((r.count / totalExits) * 100).toFixed(1)}%` : "—"}</td>
              </tr>
            ))}
          </DataCard>

          <section className="card">
            <h2>Fee drag (all-time)</h2>
            <div className="stats-grid">
              <div className="stat-tile">
                <span className="stat-label">gross credit</span>
                <span className="stat-value">{a !== undefined ? fmtMoney(a.feeDrag.grossCredit) : "—"}</span>
              </div>
              <div className="stat-tile">
                <span className="stat-label">total fees</span>
                <span className="stat-value pnl-neg">{a !== undefined ? fmtMoney(a.feeDrag.fees) : "—"}</span>
              </div>
              <div className="stat-tile">
                <span className="stat-label">net P&L</span>
                <span className={`stat-value ${(a?.feeDrag.netPnl ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}`}>
                  {a !== undefined ? fmtMoney(a.feeDrag.netPnl) : "—"}
                </span>
              </div>
              <div className="stat-tile">
                <span className="stat-label">fee drag</span>
                <span className="stat-value">{a?.feeDrag.dragPct != null ? `${a.feeDrag.dragPct.toFixed(1)}%` : "—"}</span>
              </div>
            </div>
          </section>
        </div>

        <DataCard
          title="Iron condor trades"
          headers={["date", "entry", "sym", "put", "call", "wing", "credit", "qty", "status", "P&L", "exit reason"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.trades.length ?? 0}
          skeletonRows={10}
        >
          {data?.trades.map((t) => (
            <tr key={`${t.mode}-${t.id}`}>
              <td>{t.tradeDate}</td>
              <td className="muted">{t.entryTime?.slice(11, 16) ?? "—"}</td>
              <td>{t.symbol}</td>
              <td>{fmtNum(t.putStrike, 0)}</td>
              <td>{fmtNum(t.callStrike, 0)}</td>
              <td>{fmtNum(t.wingWidth, 0)}</td>
              <td>{fmtMoney(t.netCredit)}</td>
              <td>{fmtNum(t.quantity, 0)}</td>
              <td>{t.status}</td>
              <td><PnlCell v={t.pnl} /></td>
              <td className="muted">{t.exitReason ?? "—"}</td>
            </tr>
          ))}
        </DataCard>

        <MeicDeepCards mode={mode} />

        <DataCard
          title="Daily summaries"
          headers={["date", "sym", "entries", "filled", "stopped", "win %", "net P&L"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.summaries.length ?? 0}
        >
          {data?.summaries.map((s) => (
            <tr key={`${s.summaryDate}-${s.symbol}`}>
              <td>{s.summaryDate}</td>
              <td>{s.symbol ?? "—"}</td>
              <td>{fmtNum(s.totalEntries, 0)}</td>
              <td>{fmtNum(s.entriesFilled, 0)}</td>
              <td>{fmtNum(s.entriesStopped, 0)}</td>
              <td>{fmtNum(s.winRatePct, 1)}</td>
              <td><PnlCell v={s.netPnl} /></td>
            </tr>
          ))}
        </DataCard>
      </div>
    </div>
  );
}
