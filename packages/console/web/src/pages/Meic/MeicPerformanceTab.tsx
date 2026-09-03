import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { Card, DataCard, PnlCell, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { SeriesLegend, SERIES_COLORS } from "../../components/Charts";
import { TimeLineChart } from "../../components/chart/TimeLineChart";
import { TimeBarChart } from "../../components/chart/TimeBarChart";
import { TabStrip } from "../../components/ScopeBar";
import { powerNote, WithheldNote } from "../../lib/power";

interface BreakdownRow {
  bucket: string;
  trades: number;
  sessions: number;
  winPct: number | null;
  avgNet: number | null;
}

interface Performance {
  profiles: Array<{
    profile: string;
    trades: number;
    sessions: number;
    grossPnl: number;
    fees: number;
    netPnl: number;
    winRatePct: number | null;
    expectancy: number | null;
    profitFactor: number | null;
    maxDrawdown: number;
  }>;
  equity: Array<{ date: string; netPnl: number; equity: number; drawdown: number }>;
  risk: {
    sharpe: number | null;
    sortino: number | null;
    calmar: number | null;
    recoveryFactor: number | null;
    sampleSize: number;
    sharpeOverfitFlag: boolean;
  };
  periods: Array<{
    period: string;
    trades: number;
    netPnl: number;
    cumulative: number;
    winRatePct: number | null;
    profitFactor: number | null;
    avgWin: number | null;
    avgLoss: number | null;
    expectancy: number | null;
  }>;
  studyArms: Array<{ arm: string; points: Array<{ date: string; cumulative: number }> }>;
  bySession: BreakdownRow[];
  byIvRank: BreakdownRow[];
  regimeCoverage: Array<{ dimension: string; tagged: number; untagged: number; coveragePct: number; degenerate: boolean }>;
}

const GRANULARITIES = ["daily", "weekly", "monthly"] as const;

export function MeicPerformanceTab({
  mode,
  symbol,
  profile,
  era = null,
}: {
  mode: TradingMode;
  symbol: string | null;
  profile: string | null;
  era?: string | null;
}) {
  const [granularity, setGranularity] = useState<(typeof GRANULARITIES)[number]>("daily");
  const params = new URLSearchParams({ mode, granularity });
  if (symbol !== null) params.set("symbol", symbol);
  if (profile !== null) params.set("profile", profile);
  if (era !== null) params.set("era", era);
  const { data, isLoading, dataUpdatedAt } = useQuery<Performance>({
    queryKey: ["meic-performance", mode, granularity, symbol, profile, era],
    queryFn: async () => {
      const res = await fetch(`/api/meic/performance?${params.toString()}`);
      if (!res.ok) throw new Error(`meic performance: HTTP ${res.status}`);
      return (await res.json()) as Performance;
    },
    refetchInterval: 60_000,
  });

  const risk = data?.risk;
  const equity = data?.equity ?? [];

  return (
    <div className="cards cards-wide">
      <Card title="Risk-adjusted metrics ($100k bankroll, 252-day annualization)" updatedAt={dataUpdatedAt}>
        <div className="stats-grid">
          {[
            ["sharpe", risk?.sharpe],
            ["sortino", risk?.sortino],
            ["calmar", risk?.calmar],
            ["recovery factor", risk?.recoveryFactor],
          ].map(([label, v]) => (
            <div key={String(label)} className="stat-tile">
              <span className="stat-label">{String(label)}</span>
              <span className="stat-value">{typeof v === "number" ? v.toFixed(2) : "—"}</span>
            </div>
          ))}
          <div className="stat-tile">
            <span className="stat-label">sample (days)</span>
            <span className="stat-value">{risk?.sampleSize ?? "—"}</span>
          </div>
        </div>
        {risk !== undefined && (risk.sharpeOverfitFlag || (risk.sampleSize > 0 && risk.sampleSize < 30)) && (
          <p className="stale-note" style={{ marginBottom: 0 }}>
            {risk.sharpeOverfitFlag && "Sharpe > 3 reads as a curve-fit warning, not a stronger pass. "}
            {risk.sampleSize < 30 && `Only ${risk.sampleSize} sessions — ratio metrics are not yet meaningful.`}
          </p>
        )}
      </Card>

      <Card title="Profile comparison (every profile, ranked by net — the variance-test payoff)" updatedAt={dataUpdatedAt}>
        <div className="table-scroll">
          <table className="data-table num-from-1">
            <thead>
              <tr>
                <th>profile</th><th>trades</th><th>sessions</th><th>gross</th><th>fees</th><th>net</th>
                <th>win %</th><th>expectancy</th><th>PF</th><th>max DD</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr><td colSpan={10}><span className="skeleton skeleton-text" style={{ width: "60%" }} /></td></tr>
              ) : data?.profiles.length === 0 ? (
                <tr><td colSpan={10} className="muted">no profile-tagged trades (live DB has no risk_profile column)</td></tr>
              ) : (
                data?.profiles.map((p) => (
                  <tr key={p.profile}>
                    <td>{p.profile}</td>
                    <td>{p.trades}</td>
                    <td className="muted">{p.sessions}</td>
                    <td>{fmtMoney(p.grossPnl)}</td>
                    <td className="pnl-neg">{fmtMoney(p.fees)}</td>
                    <td><PnlCell v={p.netPnl} /></td>
                    <td>{fmtPct(p.winRatePct)}</td>
                    <td>{p.expectancy !== null ? fmtMoney(p.expectancy) : "—"}</td>
                    <td>{p.profitFactor !== null ? p.profitFactor.toFixed(2) : "—"}</td>
                    <td className="pnl-neg">{fmtMoney(-p.maxDrawdown)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Labelled as cumulative NET, not "equity on a $100k base". The base is a display constant,
          not a bankroll these arms trade: `open` and `width-5` each took 265 one-lot entries in a
          single session, which is a sampling stream rather than a book. Calling the line equity
          implies position sizing and compounding this experiment deliberately does not do, and it
          is the most authoritative-looking chart on the page. The drawdown is real and stays. */}
      <Card title="Cumulative net P&L and drawdown (1-lot samples, not a sized book)" updatedAt={dataUpdatedAt}>
        {isLoading ? (
          <span className="skeleton skeleton-text" style={{ width: "40%" }} />
        ) : (
          <>
            <TimeLineChart
              series={[{ label: "equity", color: "#43b57a", points: equity.map((e) => ({ x: e.date, y: e.equity })) }]}
            />
            <TimeLineChart
              height={120}
              series={[
                {
                  label: "drawdown",
                  color: "#d95c4a",
                  fill: "rgba(217, 92, 74, 0.2)",
                  points: equity.map((e) => ({ x: e.date, y: -e.drawdown })),
                },
              ]}
            />
            <SeriesLegend items={[{ label: "equity", color: "#43b57a" }, { label: "underwater", color: "#d95c4a" }]} />
          </>
        )}
      </Card>

      <Card
        title="Per-period performance"
        updatedAt={dataUpdatedAt}
        controls={<TabStrip tabs={GRANULARITIES} value={granularity} onChange={setGranularity} />}
      >
        {isLoading ? (
          <span className="skeleton skeleton-text" style={{ width: "40%" }} />
        ) : (
          <>
            <TimeBarChart
              bars={(data?.periods ?? []).map((p) => ({ x: p.period, y: p.netPnl }))}
              overlay={(data?.periods ?? []).map((p) => ({ x: p.period, y: p.cumulative }))}
            />
            <SeriesLegend items={[{ label: "period net", color: "#43b57a" }, { label: "cumulative", color: "#7aa2ff" }]} />
            <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(23rem, 1fr))", marginTop: "0.8rem" }}>
              <div>
                <h2>win rate trend</h2>
                <TimeLineChart
                  height={130}
                  yFormat={(v) => `${v.toFixed(0)}%`}
                  series={[{ label: "win %", color: "#43b57a", points: (data?.periods ?? []).filter((p) => p.winRatePct !== null).map((p) => ({ x: p.period, y: p.winRatePct! })) }]}
                />
              </div>
              <div>
                <h2>expectancy per trade</h2>
                <TimeLineChart
                  height={130}
                  series={[{ label: "expectancy", color: "#d9a13b", points: (data?.periods ?? []).filter((p) => p.expectancy !== null).map((p) => ({ x: p.period, y: p.expectancy! })) }]}
                />
              </div>
              <div>
                <h2>avg win vs avg loss</h2>
                <TimeLineChart
                  height={130}
                  series={[
                    { label: "avg win", color: "#43b57a", points: (data?.periods ?? []).filter((p) => p.avgWin !== null).map((p) => ({ x: p.period, y: p.avgWin! })) },
                    { label: "avg loss", color: "#d95c4a", points: (data?.periods ?? []).filter((p) => p.avgLoss !== null).map((p) => ({ x: p.period, y: p.avgLoss! })) },
                  ]}
                />
              </div>
              <div>
                <h2>trade count</h2>
                <TimeBarChart height={130} yFormat={(v) => v.toFixed(0)} bars={(data?.periods ?? []).map((p) => ({ x: p.period, y: p.trades }))} />
              </div>
            </div>
          </>
        )}
      </Card>

      <Card title="Study arms — cumulative net per profile (ignores the symbol and profile scope, not the era)" updatedAt={dataUpdatedAt}>
        {isLoading ? (
          <span className="skeleton skeleton-text" style={{ width: "40%" }} />
        ) : (data?.studyArms.length ?? 0) === 0 ? (
          <p className="muted">no profile-tagged history</p>
        ) : (
          <>
            <TimeLineChart
              series={(data?.studyArms ?? []).map((a, i) => ({
                label: a.arm,
                color: SERIES_COLORS[i % SERIES_COLORS.length],
                points: a.points.map((p) => ({ x: p.date, y: p.cumulative })),
              }))}
            />
            <SeriesLegend
              items={(data?.studyArms ?? []).map((a, i) => ({ label: a.arm, color: SERIES_COLORS[i % SERIES_COLORS.length]! }))}
            />
          </>
        )}
      </Card>

      <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(23rem, 1fr))" }}>
        {powerNote(data?.bySession ?? []) !== null ? (
          <section className="card">
            <h2>Win rate by session quality</h2>
            <WithheldNote note={powerNote(data?.bySession ?? [])!} />
          </section>
        ) : (
        <DataCard
          title="Win rate by session quality"
          headers={["session", "trades", "sessions", "win %", "avg net"]}
          numFrom={1}
          tableClass="data-table-labelled"
          loading={isLoading}
          rowCount={data?.bySession.length ?? 0}
          updatedAt={dataUpdatedAt}
        >
          {data?.bySession.map((r) => (
            <tr key={r.bucket}>
              <td>{r.bucket}</td>
              <td>{r.trades}</td>
              <td className="muted">{r.sessions}</td>
              <td>{fmtPct(r.winPct)}</td>
              <td>{r.avgNet !== null ? <PnlCell v={r.avgNet} /> : "—"}</td>
            </tr>
          ))}
        </DataCard>
        )}

        {powerNote(data?.byIvRank ?? []) !== null ? (
          <section className="card">
            <h2>Avg P&L by IV-rank band</h2>
            <WithheldNote note={powerNote(data?.byIvRank ?? [])!} />
          </section>
        ) : (
        <DataCard
          title="Avg P&L by IV-rank band"
          headers={["IV rank", "trades", "sessions", "win %", "avg net"]}
          numFrom={1}
          tableClass="data-table-labelled"
          loading={isLoading}
          rowCount={data?.byIvRank.length ?? 0}
          updatedAt={dataUpdatedAt}
        >
          {data?.byIvRank.map((r) => (
            <tr key={r.bucket}>
              <td>{r.bucket}</td>
              <td>{r.trades}</td>
              <td className="muted">{r.sessions}</td>
              <td>{fmtPct(r.winPct)}</td>
              <td>{r.avgNet !== null ? <PnlCell v={r.avgNet} /> : "—"}</td>
            </tr>
          ))}
        </DataCard>
        )}

        <DataCard
          title="Regime coverage"
          headers={["dimension", "tagged", "untagged", "coverage", ""]}
          numFrom={1}
          tableClass="data-table-labelled"
          loading={isLoading}
          rowCount={data?.regimeCoverage.length ?? 0}
          updatedAt={dataUpdatedAt}
        >
          {data?.regimeCoverage.map((r) => (
            <tr key={r.dimension}>
              <td>{r.dimension}</td>
              <td>{r.tagged}</td>
              <td className="muted">{r.untagged}</td>
              <td className={r.coveragePct < 50 ? "pnl-neg" : ""}>{fmtNum(r.coveragePct, 0)}%</td>
              <td>{r.degenerate && <span className="chain-badge chain-badge-short">degenerate</span>}</td>
            </tr>
          ))}
        </DataCard>
      </div>
    </div>
  );
}
