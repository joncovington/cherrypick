import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ScreenRejections } from "./ScreenRejections";
import type { TradingMode } from "@console/shared";
import { Card, DataCard, PnlCell, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { LineChart, SeriesLegend } from "../../components/Charts";
import { TimeLineChart } from "../../components/chart/TimeLineChart";
import { TabStrip } from "../../components/ScopeBar";

interface StrategyDetail {
  strategy: string;
  trades: number;
  winRatePct: number | null;
  profitFactor: number | null;
  expectancy: number | null;
  net: number;
  sharpe: number | null;
  maxDrawdown: number;
  maxDrawdownPct: number | null;
  avgIvCrushPts: number | null;
  ivSample: number;
  sampleProgress: number;
  significant: boolean;
  directional: boolean;
  curve: Array<{ i: number; equity: number; drawdown: number }>;
}

interface Detail {
  equity: Array<{ date: string; net: number; cumulative: number }>;
  perStrategy: StrategyDetail[];
  regimeHeat: {
    ivRvBuckets: string[];
    dispersionBuckets: string[];
    cells: Array<{ strategy: string; ivRv: string; dispersion: string; trades: number }>;
  };
  capitalAtRisk: number;
}

const WINDOWS = ["cumulative", "rolling 4w", "rolling 1w"] as const;

/** Sample-progress bar: n/target, colored once the significance gates are met. */
function SampleBar({ s }: { s: StrategyDetail }) {
  const pct = Math.round(s.sampleProgress * 100);
  const color = s.significant ? "var(--ok)" : s.directional ? "var(--warn)" : "var(--border)";
  return (
    <div title={`${s.trades}/30 trades — ${s.significant ? "significant" : s.directional ? "directional only" : "too small to read"}`}>
      <div style={{ height: 6, background: "var(--row-line)", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color }} />
      </div>
      <span className="muted" style={{ fontSize: 10.5 }}>
        {s.trades}/30 {s.significant ? "significant" : s.directional ? "directional" : "thin"}
      </span>
    </div>
  );
}

export function EarningsDetailCards({ mode, era }: { mode: TradingMode; era: string | null }) {
  const [window, setWindow] = useState<(typeof WINDOWS)[number]>("cumulative");
  const { data, isLoading, dataUpdatedAt } = useQuery<Detail>({
    queryKey: ["earnings-detail", mode, era],
    queryFn: async () => {
      const res = await fetch(`/api/earnings/detail?mode=${mode}${era !== null ? `&era=${era}` : ""}`);
      if (!res.ok) throw new Error(`earnings detail: HTTP ${res.status}`);
      return (await res.json()) as Detail;
    },
    refetchInterval: 60_000,
  });

  const equity = data?.equity ?? [];
  const cutoffDays = window === "rolling 4w" ? 28 : window === "rolling 1w" ? 7 : null;
  const windowed =
    cutoffDays === null
      ? equity
      : (() => {
          const cutoff = Date.now() - cutoffDays * 86_400_000;
          const rows = equity.filter((e) => Date.parse(e.date + "T00:00:00Z") >= cutoff);
          let c = 0;
          return rows.map((e) => {
            c += e.net;
            return { ...e, cumulative: c };
          });
        })();

  const heat = data?.regimeHeat;
  const maxCell = Math.max(...(heat?.cells.map((c) => c.trades) ?? [0]), 1);
  const strategies = [...new Set(heat?.cells.map((c) => c.strategy) ?? [])].sort();

  return (
    <>
      <Card
        title="Portfolio net P&L"
        updatedAt={dataUpdatedAt}
        controls={<TabStrip tabs={WINDOWS} value={window} onChange={setWindow} />}
      >
        {isLoading ? (
          <span className="skeleton skeleton-text" style={{ width: "40%" }} />
        ) : (
          <>
            <TimeLineChart
              series={[
                {
                  label: "cum net P&L",
                  color: "#43b57a",
                  fill: "rgba(67, 181, 122, 0.15)",
                  points: windowed.map((e) => ({ x: e.date, y: e.cumulative })),
                },
              ]}
            />
            <p className="muted" style={{ fontSize: 11.5, margin: "0.3rem 0 0" }}>
              {windowed.length} closed session{windowed.length === 1 ? "" : "s"} in this window
              {data !== undefined && data.capitalAtRisk > 0 && ` · ${fmtMoney(data.capitalAtRisk)} capital at risk in open positions`}
            </p>
          </>
        )}
      </Card>

      <DataCard
        title="Per-strategy detail"
        headers={["strategy", "trades", "win %", "PF", "expectancy", "net", "Sharpe (trade)", "max DD", "IV crush", "sample"]}
        numFrom={1}
        loading={isLoading}
        rowCount={data?.perStrategy.length ?? 0}
        updatedAt={dataUpdatedAt}
      >
        {data?.perStrategy.map((s) => (
          <tr key={s.strategy}>
            <td>{s.strategy}</td>
            <td>{s.trades}</td>
            <td>{fmtPct(s.winRatePct)}</td>
            <td className={s.profitFactor !== null && s.profitFactor >= 1 ? "pnl-pos" : "pnl-neg"}>
              {s.profitFactor !== null ? `${s.profitFactor.toFixed(2)} ${s.profitFactor >= 1 ? "✓" : "✗"}` : "—"}
            </td>
            <td>{s.expectancy !== null ? <PnlCell v={s.expectancy} /> : "—"}</td>
            <td><PnlCell v={s.net} /></td>
            <td>{s.sharpe !== null ? s.sharpe.toFixed(2) : "—"}</td>
            <td className="pnl-neg">
              {fmtMoney(-s.maxDrawdown)}
              {s.maxDrawdownPct !== null && <span className="muted"> ({s.maxDrawdownPct.toFixed(1)}%)</span>}
            </td>
            <td title={`${s.ivSample} trades with both entry and exit IV`}>
              {s.avgIvCrushPts !== null ? `${s.avgIvCrushPts.toFixed(1)} pts` : "—"}
            </td>
            <td style={{ minWidth: "7rem" }}><SampleBar s={s} /></td>
          </tr>
        ))}
      </DataCard>

      <Card title="Per-strategy equity and drawdown" updatedAt={dataUpdatedAt}>
        {isLoading ? (
          <span className="skeleton skeleton-text" style={{ width: "40%" }} />
        ) : (
          <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(22rem, 1fr))" }}>
            {data?.perStrategy.map((s) => (
              <div key={s.strategy}>
                <h2>
                  {s.strategy} · {s.trades} trades
                </h2>
                <LineChart
                  height={130}
                  series={[
                    { label: "equity", color: "#43b57a", points: s.curve.map((c) => ({ x: String(c.i).padStart(4, "0"), y: c.equity })) },
                    {
                      label: "drawdown",
                      color: "#d95c4a",
                      fill: "rgba(217, 92, 74, 0.18)",
                      points: s.curve.map((c) => ({ x: String(c.i).padStart(4, "0"), y: -c.drawdown })),
                    },
                  ]}
                />
              </div>
            ))}
          </div>
        )}
        <SeriesLegend items={[{ label: "equity", color: "#43b57a" }, { label: "drawdown from peak", color: "#d95c4a" }]} />
      </Card>

      <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(22rem, 1fr))" }}>
        <Card title="Regime coverage — where the sample actually lives" updatedAt={dataUpdatedAt}>
          {heat === undefined || heat.cells.length === 0 ? (
            <p className="muted">no tagged trades yet</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>strategy</th>
                    {heat.ivRvBuckets.flatMap((iv) => heat.dispersionBuckets.map((dp) => <th key={`${iv}-${dp}`}>{iv} / {dp}</th>))}
                  </tr>
                </thead>
                <tbody>
                  {strategies.map((st) => (
                    <tr key={st}>
                      <td>{st}</td>
                      {heat.ivRvBuckets.flatMap((iv) =>
                        heat.dispersionBuckets.map((dp) => {
                          const n = heat.cells.find((c) => c.strategy === st && c.ivRv === iv && c.dispersion === dp)?.trades ?? 0;
                          return (
                            <td
                              key={`${iv}-${dp}`}
                              style={{
                                textAlign: "center",
                                background: n > 0 ? `rgba(122, 162, 255, ${0.12 + 0.6 * (n / maxCell)})` : undefined,
                              }}
                            >
                              {n > 0 ? n : ""}
                            </td>
                          );
                        }),
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="muted" style={{ fontSize: 11, margin: "0.4rem 0 0" }}>
            columns are IV/RV ratio × realized-move dispersion at entry
          </p>
        </Card>

        <ScreenRejections mode={mode} era={era} />
      </div>

      <Card title="Reading these numbers" collapseKey="earnings-caveats">
        <ul className="muted" style={{ fontSize: 12, margin: 0, paddingLeft: "1.1rem", lineHeight: 1.6 }}>
          <li>
            Sample sizes are small. A strategy under 30 closed trades is directional at best; the bars above say which
            have earned a read and which have not.
          </li>
          <li>
            Earnings trades cluster on the same dates and the same macro tape, so trades are not independent — win rates
            and profit factors overstate their own confidence.
          </li>
          <li>
            Paper fills are modeled at mid with a slippage haircut. Real entries on wide earnings spreads will fill
            worse, and that gap grows with the illiquid names.
          </li>
          <li>IV crush is entry IV minus exit IV in volatility points, measured only where both were recorded.</li>
        </ul>
      </Card>
    </>
  );
}
