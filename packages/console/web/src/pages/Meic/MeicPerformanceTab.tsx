import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { Card, PnlCell, SortTh, fmtMoney, fmtNum, fmtPct, sortRows, useSort } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { SeriesLegend, SERIES_COLORS } from "../../components/Charts";
import { TimeLineChart } from "../../components/chart/TimeLineChart";
import { TimeBarChart } from "../../components/chart/TimeBarChart";
import { TabStrip } from "../../components/ScopeBar";
import { Section } from "../../components/Section";
import { powerNote, WithheldNote } from "../../lib/power";
import { DailyRiskTiles, dailyRiskFlag, dailyRiskNote } from "../../components/performance/DailyRiskTiles";
import { tokenAlpha } from "../../components/chart/cssvars";
import { useOpenParam } from "../Backtest/CardLightbox";
import { BoxOpen, FliesBoxHost, type FliesBox } from "../Flies/FliesBoxes";

interface BreakdownRow {
  bucket: string;
  trades: number;
  sessions: number;
  winPct: number | null;
  avgNet: number | null;
}

interface Performance {
  arms: Array<{
    arm: string;
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
    undersampledFlag: boolean;
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

type ArmRow = Performance["arms"][number];

const ARM_SORTS = {
  arm: (p: ArmRow) => p.arm,
  trades: (p: ArmRow) => p.trades,
  sessions: (p: ArmRow) => p.sessions,
  gross: (p: ArmRow) => p.grossPnl,
  fees: (p: ArmRow) => p.fees,
  net: (p: ArmRow) => p.netPnl,
  win: (p: ArmRow) => p.winRatePct,
  expectancy: (p: ArmRow) => p.expectancy,
  pf: (p: ArmRow) => p.profitFactor,
  dd: (p: ArmRow) => p.maxDrawdown,
};

const ARMS_HELP: CardHelpContent = {
  title: "Arm comparison",
  body: (
    <HelpSection title="Every arm">
      <p>
        Every arm, ranked by net: the variance test&apos;s payoff. A header click re-ranks, and a third click returns to the server&apos;s order. Max
        DD sorts by the drawdown&apos;s size, deepest first.
      </p>
    </HelpSection>
  ),
};

/* Cumulative NET, not "equity on a $100k base": the base is a display constant, not a bankroll these
   arms trade (an arm can take hundreds of one-lot entries in a session, a sampling stream rather than
   a book). The drawdown is real and stays. */
const CUMULATIVE_HELP: CardHelpContent = {
  title: "Cumulative net and drawdown",
  body: (
    <HelpWarn title="1-lot samples, not a sized book">
      <p>
        The base under the curve is a drawing constant. These arms take one-lot entries and do not compound, so the line is cumulative net, not
        equity. The drawdown below it is real.
      </p>
    </HelpWarn>
  ),
};

const PERIOD_HELP: CardHelpContent = {
  title: "Per period",
  body: (
    <HelpSection title="Bars and line">
      <p>Each period&apos;s net as a bar, the cumulative as the line. The trends open the same periods&apos; win rate, expectancy, average win and loss, and trade count.</p>
    </HelpSection>
  ),
};

const STUDY_HELP: CardHelpContent = {
  title: "Study arms",
  body: (
    <HelpWarn title="Wider than the page's scope">
      <p>Cumulative net per arm over every arm-tagged trade. It ignores the symbol and arm picked in the header, but not the era.</p>
    </HelpWarn>
  ),
};

const BUCKET_HELP: CardHelpContent = {
  title: "Breakdowns",
  body: (
    <>
      <HelpSection title="What was known at entry">
        <p>Win rate and average net by the session&apos;s quality and by the IV-rank band at entry. A breakdown too thin to read is withheld and says why.</p>
      </HelpSection>
      <HelpSection title="Regime coverage">
        <p>How many trades carry each regime tag. Under half is shown in red; a degenerate dimension has one value for nearly every trade, so it cannot split them.</p>
      </HelpSection>
    </>
  ),
};

function BucketTable({ rows, head }: { rows: BreakdownRow[]; head: string }) {
  const note = powerNote(rows);
  if (note !== null) return <WithheldNote note={note} />;
  return (
    <div className="table-scroll">
      <table className="data-table data-table-labelled num-from-1">
        <thead>
          <tr>
            <th>{head}</th>
            <th>trades</th>
            <th>sessions</th>
            <th>win %</th>
            <th>avg net</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.bucket}>
              <td>{r.bucket}</td>
              <td>{r.trades}</td>
              <td className="muted">{r.sessions}</td>
              <td>{fmtPct(r.winPct)}</td>
              <td>{r.avgNet !== null ? <PnlCell v={r.avgNet} /> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Trends({ periods }: { periods: Performance["periods"] }) {
  return (
    <div className="pf-row">
      <div className="card">
        <h3 className="card-subhead">win rate</h3>
        <TimeLineChart
          height={150}
          yFormat={(v) => `${v.toFixed(0)}%`}
          series={[{ label: "win %", color: "var(--pos)", points: periods.filter((p) => p.winRatePct !== null).map((p) => ({ x: p.period, y: p.winRatePct! })) }]}
        />
      </div>
      <div className="card">
        <h3 className="card-subhead">expectancy per trade</h3>
        <TimeLineChart
          height={150}
          series={[{ label: "expectancy", color: "var(--amber-line)", points: periods.filter((p) => p.expectancy !== null).map((p) => ({ x: p.period, y: p.expectancy! })) }]}
        />
      </div>
      <div className="card">
        <h3 className="card-subhead">average win and loss</h3>
        <TimeLineChart
          height={150}
          series={[
            { label: "avg win", color: "var(--pos)", points: periods.filter((p) => p.avgWin !== null).map((p) => ({ x: p.period, y: p.avgWin! })) },
            { label: "avg loss", color: "var(--neg)", points: periods.filter((p) => p.avgLoss !== null).map((p) => ({ x: p.period, y: p.avgLoss! })) },
          ]}
        />
        <SeriesLegend items={[{ label: "avg win", color: "var(--pos)" }, { label: "avg loss", color: "var(--neg)" }]} />
      </div>
      <div className="card">
        <h3 className="card-subhead">trades</h3>
        <TimeBarChart height={150} yFormat={(v) => v.toFixed(0)} bars={periods.map((p) => ({ x: p.period, y: p.trades }))} />
      </div>
    </div>
  );
}

function skeleton() {
  return <span className="skeleton skeleton-text" style={{ width: "40%" }} />;
}

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
  const lb = useOpenParam("mperf");

  // The server ranks the arms by net; a header click re-ranks, and a third click returns to that.
  const armSort = useSort();
  const risk = data?.risk;
  const equity = data?.equity ?? [];
  const periods = data?.periods ?? [];

  const boxes: FliesBox[] = [
    {
      id: "trends",
      kicker: `performance › ${granularity}`,
      title: "Trends by period",
      tabs: [{ id: "all", label: "trends" }],
      body: () => <Trends periods={periods} />,
    },
  ];

  return (
    <div className="pf-layout">
      <Section concept="hist" tag="side by side" question="How do the arms compare, risk-adjusted?">
        <Card
          title="risk-adjusted ratios"
          className="lv-12"
          collapseKey="meic-perf-risk"
          updatedAt={dataUpdatedAt}
          help={{ title: "Risk-adjusted ratios", body: dailyRiskNote(risk) }}
          flag={dailyRiskFlag(risk)}
        >
          <DailyRiskTiles risk={risk} maxDrawdown={equity.length > 0 ? Math.max(...equity.map((e) => e.drawdown), 0) : null} />
        </Card>

        <Card title="arm comparison" className="lv-12" collapseKey="meic-perf-arms" updatedAt={dataUpdatedAt} help={ARMS_HELP}>
          <div className="table-scroll">
            <table className="data-table num-from-1">
              <thead>
                <tr>
                  <SortTh label="arm" k="arm" sort={armSort} />
                  <SortTh label="trades" k="trades" sort={armSort} />
                  <SortTh label="sessions" k="sessions" sort={armSort} />
                  <SortTh label="gross" k="gross" sort={armSort} />
                  <SortTh label="fees" k="fees" sort={armSort} />
                  <SortTh label="net" k="net" sort={armSort} />
                  <SortTh label="win %" k="win" sort={armSort} />
                  <SortTh label="expectancy" k="expectancy" sort={armSort} />
                  <SortTh label="PF" k="pf" sort={armSort} />
                  <SortTh label="max DD" k="dd" sort={armSort} title="sorts by the drawdown's size: deepest first" />
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  <tr>
                    <td colSpan={10}>{skeleton()}</td>
                  </tr>
                ) : data?.arms.length === 0 ? (
                  <tr>
                    <td colSpan={10} className="muted">
                      no arm-tagged trades (this ledger has no arm column)
                    </td>
                  </tr>
                ) : (
                  sortRows(data?.arms ?? [], armSort, ARM_SORTS).map((p) => (
                    <tr key={p.arm}>
                      <td>{p.arm}</td>
                      <td>{p.trades}</td>
                      <td className="muted">{p.sessions}</td>
                      <td>{fmtMoney(p.grossPnl)}</td>
                      <td className="pnl-neg">{fmtMoney(-p.fees)}</td>
                      <td>
                        <PnlCell v={p.netPnl} />
                      </td>
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
      </Section>

      <Section concept="hist" tag="by period" question="How did it build up, period by period?">
        <Card title="cumulative net and drawdown" collapseKey="meic-perf-cumulative" updatedAt={dataUpdatedAt} help={CUMULATIVE_HELP} flag="1-lot samples">
          {isLoading ? (
            skeleton()
          ) : (
            <>
              <TimeLineChart series={[{ label: "cumulative", color: "var(--pos)", points: equity.map((e) => ({ x: e.date, y: e.equity })) }]} />
              <TimeLineChart
                height={120}
                series={[{ label: "drawdown", color: "var(--neg)", fill: tokenAlpha("--neg", 0.2), points: equity.map((e) => ({ x: e.date, y: -e.drawdown })) }]}
              />
              <SeriesLegend items={[{ label: "cumulative", color: "var(--pos)" }, { label: "underwater", color: "var(--neg)" }]} />
            </>
          )}
        </Card>

        <Card
          title="per period"
          collapseKey="meic-perf-periods"
          updatedAt={dataUpdatedAt}
          help={PERIOD_HELP}
          controls={<TabStrip tabs={GRANULARITIES} value={granularity} onChange={setGranularity} />}
        >
          {isLoading ? (
            skeleton()
          ) : (
            <>
              <TimeBarChart bars={periods.map((p) => ({ x: p.period, y: p.netPnl }))} overlay={periods.map((p) => ({ x: p.period, y: p.cumulative }))} />
              <SeriesLegend items={[{ label: "period net", color: "var(--pos)" }, { label: "cumulative", color: "var(--accent)" }]} />
              {periods.length > 0 && (
                <div className="pf-mores pf-push">
                  <BoxOpen lb={lb} id="trends">
                    win rate, expectancy, wins and losses, trades
                  </BoxOpen>
                </div>
              )}
            </>
          )}
        </Card>

        <Card title="study arms" className="lv-12" collapseKey="meic-perf-study" updatedAt={dataUpdatedAt} help={STUDY_HELP} flag="ignores the header's scope">
          {isLoading ? (
            skeleton()
          ) : (data?.studyArms.length ?? 0) === 0 ? (
            <p className="muted">no arm-tagged history</p>
          ) : (
            <>
              <TimeLineChart
                series={(data?.studyArms ?? []).map((a, i) => ({
                  label: a.arm,
                  color: SERIES_COLORS[i % SERIES_COLORS.length],
                  points: a.points.map((p) => ({ x: p.date, y: p.cumulative })),
                }))}
              />
              <SeriesLegend items={(data?.studyArms ?? []).map((a, i) => ({ label: a.arm, color: SERIES_COLORS[i % SERIES_COLORS.length]! }))} />
            </>
          )}
        </Card>
      </Section>

      <Section concept="dir" tag="what shaped it" question="When does it do better?">
        <Card title="by session quality" className="lv-4" collapseKey="meic-perf-session" updatedAt={dataUpdatedAt} help={BUCKET_HELP}>
          {isLoading ? skeleton() : <BucketTable rows={data?.bySession ?? []} head="session" />}
        </Card>
        <Card title="by IV-rank band" className="lv-4" collapseKey="meic-perf-ivr" updatedAt={dataUpdatedAt} help={BUCKET_HELP}>
          {isLoading ? skeleton() : <BucketTable rows={data?.byIvRank ?? []} head="IV rank" />}
        </Card>
        <Card title="regime coverage" className="lv-4" collapseKey="meic-perf-coverage" updatedAt={dataUpdatedAt} help={BUCKET_HELP}>
          {isLoading ? (
            skeleton()
          ) : (
            <div className="table-scroll">
              <table className="data-table data-table-labelled num-from-1">
                <thead>
                  <tr>
                    <th>dimension</th>
                    <th>tagged</th>
                    <th>untagged</th>
                    <th>coverage</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.regimeCoverage ?? []).map((r) => (
                    <tr key={r.dimension}>
                      <td>
                        {r.dimension} {r.degenerate && <span className="chain-badge chain-badge-short">degenerate</span>}
                      </td>
                      <td>{r.tagged}</td>
                      <td className="muted">{r.untagged}</td>
                      <td className={r.coveragePct < 50 ? "pnl-neg" : ""}>{fmtNum(r.coveragePct, 0)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </Section>
      <FliesBoxHost lb={lb} boxes={boxes} />
    </div>
  );
}
