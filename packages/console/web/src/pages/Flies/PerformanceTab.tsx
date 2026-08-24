import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { fliesQuery, type FliesFilter } from "../../lib/api";
import { fmtMoney } from "../../components/DataTable";
import { EquityUnderwater } from "../../components/EquityUnderwater";
import { VoidedNote } from "./VoidedNote";

interface Performance {
  tiles: {
    trades: number;
    netPnl: number;
    winRatePct: number | null;
    profitFactor: number | null;
    feeDragPct: number | null;
    completionRatePct: number | null;
  };
  series: Array<{ bucket: string; netPnl: number; cumulative: number }>;
  equity: Array<{ date: string; netPnl: number; equity: number; drawdown: number }>;
  risk: {
    sharpe: number | null;
    sortino: number | null;
    calmar: number | null;
    recoveryFactor: number | null;
    sampleSize: number;
    sharpeOverfitFlag: boolean;
  };
  roll: {
    leggedEntries: number;
    completed: number;
    completionRatePct: number | null;
    neverOffered: number;
    bufferBlocked: number;
    floorBlocked: number;
    unknown: number;
    medianLatencyMin: number | null;
    minLatencyMin: number | null;
    maxLatencyMin: number | null;
    medianSpotMove: number | null;
  } | null;
  completion: {
    leggedEntries: number;
    completed: number;
    completionRatePct: number | null;
    neverOffered: number;
    bufferBlocked: number;
    floorBlocked: number;
    unknown: number;
    medianLatencyMin: number | null;
    minLatencyMin: number | null;
    maxLatencyMin: number | null;
    medianSpotMove: number | null;
  };
  completionTrend: Array<{ day: string; legged: number; completed: number; ratePct: number | null }>;
  liveVsPaper: {
    arm: string;
    live: { sessions: number; entries: number; completed: number; completionRatePct: number | null; medianLatencyMin: number | null; avgCredit: number | null };
    paper: { sessions: number; entries: number; completed: number; completionRatePct: number | null; medianLatencyMin: number | null; avgCredit: number | null };
    completionGapPct: number | null;
    abort: { minLiveEntries: number; gapLimitPct: number; armed: boolean; triggered: boolean };
  } | null;
  leftOnTable: {
    entryMode: "debit_first";
    untracked: number;
    overall: LeftOnTableSummary;
    byGexBucket: Record<string, LeftOnTableSummary>;
  } | null;
}

interface LeftOnTableSummary {
  n: number;
  improved: number;
  medianImprovementPts: number | null;
  maxImprovementPts: number | null;
  medianImprovementDollars: number | null;
  totalImprovementDollars: number | null;
}

const GRANULARITIES = ["daily", "weekly", "monthly"] as const;

function usePerformance(mode: TradingMode, granularity: string, filter: FliesFilter) {
  return useQuery<Performance>({
    queryKey: ["flies-performance", mode, granularity, filter.arm, filter.symbol, filter.era],
    queryFn: async () => {
      const res = await fetch(
        `/api/flies/performance?${fliesQuery(mode, { ...filter, date: null })}&granularity=${granularity}`,
      );
      if (!res.ok) throw new Error(`performance: HTTP ${res.status}`);
      return (await res.json()) as Performance;
    },
    refetchInterval: 60_000,
  });
}

function Tile({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" | "dim" }) {
  const cls = tone === "pos" ? "pnl-pos" : tone === "neg" ? "pnl-neg" : tone === "dim" ? "muted" : "";
  return (
    <div className="stat-tile">
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${cls}`}>{value}</span>
    </div>
  );
}

/** A ratio, or an em-dash where it is undefined. Never 0 — see riskMetrics.ts on why. */
function fmtRatio(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}

function tone(v: number | null): "pos" | "neg" | "dim" | undefined {
  if (v === null) return "dim";
  return v >= 0 ? "pos" : "neg";
}

/** P&L over time bars with a cumulative overlay line. */
function PnlBars({ series, cumulative }: { series: Performance["series"]; cumulative: boolean }) {
  if (series.length === 0) return <p className="muted">not enough history yet</p>;
  const width = 1150;
  const height = 220;
  const m = { l: 56, r: 12, t: 10, b: 20 };
  const nets = series.map((s) => s.netPnl);
  const cums = series.map((s) => s.cumulative);
  const yMin = Math.min(...nets, ...(cumulative ? cums : []), 0);
  const yMax = Math.max(...nets, ...(cumulative ? cums : []), 0);
  const span = yMax - yMin || 1;
  const Y = (v: number) => m.t + ((yMax - v) / span) * (height - m.t - m.b);
  const bw = (width - m.l - m.r) / series.length;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="P&L over time" style={{ width: "100%", height: "auto", display: "block" }}>
      <line x1={m.l} y1={Y(0)} x2={width - m.r} y2={Y(0)} stroke="#3d4653" />
      {[yMax, 0, yMin].map((v, i) => (
        <text key={i} x={4} y={Y(v) + 3} fontSize={9} fill="#82878f" fontFamily="Consolas, monospace">{fmtMoney(v)}</text>
      ))}
      {series.map((s, i) => (
        <rect
          key={s.bucket}
          x={m.l + i * bw + bw * 0.15}
          y={Math.min(Y(0), Y(s.netPnl))}
          width={Math.max(bw * 0.7, 1)}
          height={Math.max(Math.abs(Y(s.netPnl) - Y(0)), 1)}
          fill={s.netPnl >= 0 ? "#43b57a" : "#d95c4a"}
        >
          <title>{`${s.bucket}: ${fmtMoney(s.netPnl)} (cum ${fmtMoney(s.cumulative)})`}</title>
        </rect>
      ))}
      {cumulative && (
        <polyline
          points={series.map((s, i) => `${(m.l + (i + 0.5) * bw).toFixed(1)},${Y(s.cumulative).toFixed(1)}`).join(" ")}
          fill="none"
          stroke="#7aa2ff"
          strokeWidth={1.6}
        />
      )}
      {series.length > 0 && (
        <>
          <text x={m.l} y={height - 6} fontSize={9} fill="#82878f" fontFamily="Consolas, monospace">{series[0]!.bucket}</text>
          <text x={width - m.r} y={height - 6} fontSize={9} fill="#82878f" textAnchor="end" fontFamily="Consolas, monospace">
            {series[series.length - 1]!.bucket}
          </text>
        </>
      )}
    </svg>
  );
}

/** Per-session completion rate — the number that decides whether the strategy is real, on a trend. */
function CompletionTrend({ trend }: { trend: Performance["completionTrend"] }) {
  if (trend.length === 0) return <p className="muted">no legged sessions yet</p>;
  const width = 1150;
  const height = 130;
  const m = { l: 40, r: 12, t: 8, b: 18 };
  const X = (i: number) => m.l + (i / Math.max(trend.length - 1, 1)) * (width - m.l - m.r);
  const Y = (pct: number) => m.t + ((100 - pct) / 100) * (height - m.t - m.b);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="completion trend" style={{ width: "100%", height: "auto", display: "block" }}>
      {[0, 50, 100].map((p) => (
        <g key={p}>
          <line x1={m.l} y1={Y(p)} x2={width - m.r} y2={Y(p)} stroke="#15181e" />
          <text x={4} y={Y(p) + 3} fontSize={9} fill="#82878f" fontFamily="Consolas, monospace">{p}%</text>
        </g>
      ))}
      <polyline
        points={trend.filter((t) => t.ratePct !== null).map((t, i) => `${X(i).toFixed(1)},${Y(t.ratePct!).toFixed(1)}`).join(" ")}
        fill="none"
        stroke="#43b57a"
        strokeWidth={1.6}
      />
      {trend.map((t, i) =>
        t.ratePct !== null ? (
          <circle key={t.day} cx={X(i)} cy={Y(t.ratePct)} r={2.5} fill="#43b57a">
            <title>{`${t.day}: ${t.completed}/${t.legged} (${t.ratePct.toFixed(0)}%)`}</title>
          </circle>
        ) : null,
      )}
      <text x={m.l} y={height - 4} fontSize={9} fill="#82878f" fontFamily="Consolas, monospace">{trend[0]!.day}</text>
      <text x={width - m.r} y={height - 4} fontSize={9} fill="#82878f" textAnchor="end" fontFamily="Consolas, monospace">{trend[trend.length - 1]!.day}</text>
    </svg>
  );
}

export function PerformanceTab({ mode, filter }: { mode: TradingMode; filter: FliesFilter }) {
  const [granularity, setGranularity] = useState<(typeof GRANULARITIES)[number]>("daily");
  const [cumulative, setCumulative] = useState(true);
  // `date` dropped: an equity curve pinned to one day is a point.
  const { data, isLoading } = usePerformance(mode, granularity, filter);
  const t = data?.tiles;
  const c = data?.completion;
  const roll = data?.roll ?? null;
  const lvp = data?.liveVsPaper ?? null;
  const lot = data?.leftOnTable ?? null;

  return (
    <div className="cards cards-wide">
      <section className="card">
        <div className="stats-grid">
          <Tile label="net P&L" value={t !== undefined ? fmtMoney(t.netPnl) : "—"} tone={t !== undefined && t.netPnl >= 0 ? "pos" : "neg"} />
          <Tile label="trades" value={String(t?.trades ?? "—")} />
          <Tile label="win rate" value={t?.winRatePct != null ? `${t.winRatePct.toFixed(0)}%` : "—"} />
          <Tile label="profit factor" value={t?.profitFactor != null ? t.profitFactor.toFixed(2) : "—"} />
          <Tile label="fee drag" value={t?.feeDragPct != null ? `${t.feeDragPct.toFixed(1)}%` : "—"} tone="dim" />
          <Tile label="completion" value={t?.completionRatePct != null ? `${t.completionRatePct.toFixed(0)}%` : "—"} />
        </div>
      </section>

      <section className="card">
        <div className="panel-head-row">
          <h2>P&L over time</h2>
          <div className="mode-toggle">
            {GRANULARITIES.map((g) => (
              <button key={g} type="button" className={granularity === g ? "mode-btn active" : "mode-btn"} onClick={() => setGranularity(g)}>
                {g}
              </button>
            ))}
          </div>
          <label className="muted lbl">
            <input type="checkbox" checked={cumulative} onChange={(e) => setCumulative(e.target.checked)} /> cumulative
          </label>
        </div>
        {isLoading ? <span className="skeleton skeleton-text" style={{ width: "40%" }} /> : <PnlBars series={data?.series ?? []} cumulative={cumulative} />}
      </section>

      <VoidedNote mode={mode} filter={filter} />

      {/* The same pair MEIC's performance tab carries, from the same shared module so the two
          cannot disagree about what a Sharpe is. Titled "1-lot samples, not a sized book" for the
          reason MEIC's is: the base under the curve is a drawing constant, these arms take one-lot
          entries and do not compound, and this is the most authoritative-looking chart on the page.
          The drawdown is real. */}
      <section className="card">
        <div className="panel-head-row">
          <h2>Cumulative net P&amp;L and drawdown (1-lot samples, not a sized book)</h2>
          <span className="muted lbl">daily, whatever granularity is selected above</span>
        </div>
        {isLoading ? (
          <span className="skeleton skeleton-text" style={{ width: "40%" }} />
        ) : (data?.equity.length ?? 0) === 0 ? (
          <p className="muted">not enough history yet</p>
        ) : (
          <>
            <EquityUnderwater equity={data?.equity ?? []} />
            <div className="stats-grid" style={{ marginTop: "0.75rem" }}>
              <Tile label="sharpe" value={fmtRatio(data?.risk.sharpe ?? null)} tone={tone(data?.risk.sharpe ?? null)} />
              <Tile label="sortino" value={fmtRatio(data?.risk.sortino ?? null)} tone={tone(data?.risk.sortino ?? null)} />
              <Tile label="calmar" value={fmtRatio(data?.risk.calmar ?? null)} tone={tone(data?.risk.calmar ?? null)} />
              <Tile
                label="max drawdown"
                value={fmtMoney(Math.max(...(data?.equity ?? []).map((e) => e.drawdown), 0))}
                tone="neg"
              />
              <Tile label="sessions" value={String(data?.risk.sampleSize ?? 0)} tone="dim" />
            </div>
            <p className="muted lbl" style={{ marginTop: "0.5rem" }}>
              Annualized on 252 sessions from {data?.risk.sampleSize ?? 0} of them. A ratio over that
              few sessions describes this stretch, not the strategy.
              {data?.risk.sharpeOverfitFlag === true && " Sharpe above 3 on a sample this small is a warning about the sample."}
            </p>
          </>
        )}
      </section>

      <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(22rem, 1fr))" }}>
        <section className="card">
          <h2>Completion (legged — the number that decides if this is real)</h2>
          {c !== undefined && (
            <table className="data-table">
              <tbody>
                <tr><td className="muted">legged entries</td><td>{c.leggedEntries}</td></tr>
                <tr><td className="muted">completed into flies</td><td>{c.completed}</td></tr>
                <tr><td className="muted">completion rate</td><td>{c.completionRatePct !== null ? `${c.completionRatePct.toFixed(1)}%` : "—"}</td></tr>
                <tr><td className="muted">median latency</td><td>{c.medianLatencyMin !== null ? `${c.medianLatencyMin.toFixed(0)}m` : "—"}</td></tr>
                <tr>
                  <td className="muted">latency range</td>
                  <td>{c.minLatencyMin !== null ? `${c.minLatencyMin.toFixed(0)}–${c.maxLatencyMin?.toFixed(0)}m` : "—"}</td>
                </tr>
                <tr><td className="muted">median spot move to complete</td><td>{c.medianSpotMove !== null ? c.medianSpotMove.toFixed(2) : "—"}</td></tr>
              </tbody>
            </table>
          )}
        </section>

        {/* The bwb arm has no completion rate: it is entered WHOLE for a credit and converted by a
            ROLL, not legged in and completed. Its own panel rather than a row in the one above, so
            neither number is read as the other. Hidden entirely when no bwb entries are in scope --
            an empty panel reading "0%" is a claim, and there is nothing to claim. */}
        {roll !== null && (
          <section className="card">
            <h2>Roll (bwb — the same question, a different trade)</h2>
            <table className="data-table">
              <tbody>
                <tr><td className="muted">bwb entries</td><td>{roll.leggedEntries}</td></tr>
                <tr><td className="muted">rolled into flies</td><td>{roll.completed}</td></tr>
                <tr><td className="muted">roll rate</td><td>{roll.completionRatePct !== null ? `${roll.completionRatePct.toFixed(1)}%` : "—"}</td></tr>
                <tr><td className="muted">median latency</td><td>{roll.medianLatencyMin !== null ? `${roll.medianLatencyMin.toFixed(0)}m` : "—"}</td></tr>
                <tr>
                  <td className="muted">latency range</td>
                  <td>{roll.minLatencyMin !== null ? `${roll.minLatencyMin.toFixed(0)}–${roll.maxLatencyMin?.toFixed(0)}m` : "—"}</td>
                </tr>
                <tr><td className="muted">median spot move to roll</td><td>{roll.medianSpotMove !== null ? roll.medianSpotMove.toFixed(2) : "—"}</td></tr>
              </tbody>
            </table>
            <p className="muted" style={{ fontSize: 12, margin: "0.4rem 0 0" }}>
              Until it rolls, a bwb carries real negative tail risk of{" "}
              <strong>wing − far wing</strong> — unlike an uncompleted legged entry, whose worst case
              is its own defined width. An unrolled bwb is not a neutral outcome.
            </p>
            {roll.leggedEntries > 0 && roll.leggedEntries < 5 && (
              <p className="muted" style={{ fontSize: 12, margin: "0.3rem 0 0" }}>
                <strong>{roll.leggedEntries} entries</strong> — too few to read as a rate.
              </p>
            )}
          </section>
        )}

        {roll !== null && (
          <section className="card">
            <h2>Why rolls missed</h2>
            <table className="data-table">
              <tbody>
                <tr><td className="muted">never cheap enough</td><td>{roll.neverOffered}</td></tr>
                <tr><td className="muted">our buffer refused it</td><td>{roll.bufferBlocked}</td></tr>
                <tr><td className="muted">our floor refused it</td><td>{roll.floorBlocked}</td></tr>
                <tr><td className="muted">unknown</td><td>{roll.unknown}</td></tr>
              </tbody>
            </table>
            <p className="muted" style={{ fontSize: 12, margin: "0.4rem 0 0" }}>
              Read against <code>best_roll_debit</code>: "the market never made the roll cheap enough"
              and "our own gate refused a roll that was offered" are identical in the P&amp;L and call
              for opposite fixes.
            </p>
          </section>
        )}

        {/* debit_first's wait-for-better hypothesis: how much better the completing price got AFTER
            the first qualifying tick, split by the dealer-gamma regime at completion -- pinning
            (positive gamma) is the regime where waiting should have paid. Hidden when the scope
            holds no debit_first completions, same convention as the roll cards above. */}
        {lot !== null && (
          <section className="card">
            <h2>Left on table (debit-first — waiting past the first qualifying tick)</h2>
            <table className="data-table">
              <tbody>
                <tr><td className="muted">completions tracked</td><td>{lot.overall.n}</td></tr>
                <tr><td className="muted">improved by waiting</td><td>{lot.overall.improved}</td></tr>
                <tr><td className="muted">median improvement</td><td>{lot.overall.medianImprovementDollars !== null ? fmtMoney(lot.overall.medianImprovementDollars) : "—"}</td></tr>
                <tr><td className="muted">total left on table</td><td>{lot.overall.totalImprovementDollars !== null ? fmtMoney(lot.overall.totalImprovementDollars) : "—"}</td></tr>
                <tr><td className="muted">untracked (pre-2026-08-03, or iron/bwb)</td><td className="muted">{lot.untracked}</td></tr>
              </tbody>
            </table>
            {Object.keys(lot.byGexBucket).length > 0 && (
              <table className="data-table data-table-labelled" style={{ marginTop: "0.6rem" }}>
                <thead><tr><th>gex bucket at completion</th><th>n</th><th>improved</th><th>median</th><th>total</th></tr></thead>
                <tbody>
                  {Object.entries(lot.byGexBucket).map(([bucket, s]) => (
                    <tr key={bucket}>
                      <td>{bucket}</td>
                      <td>{s.n}</td>
                      <td>{s.improved}</td>
                      <td>{s.medianImprovementDollars !== null ? fmtMoney(s.medianImprovementDollars) : "—"}</td>
                      <td>{s.totalImprovementDollars !== null ? fmtMoney(s.totalImprovementDollars) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="muted" style={{ fontSize: 12, margin: "0.4rem 0 0" }}>
              Improvement is floored at 0 per position — the completion tick itself seeds the tracker,
              so a price that never got better records as no improvement, not a loss. If pinning
              (positive gamma) shows the largest improvement here, that is the evidence waiting pays
              for debit-first; if the split shows no conditional difference, first-tick completion
              already captures it.
            </p>
          </section>
        )}

        <section className="card">
          <h2>Why misses missed (opposite remedies — do not lump)</h2>
          {c !== undefined && (
            <table className="data-table">
              <tbody>
                <tr>
                  <td className="muted" title="the best debit ever seen was still above the credit — no buffer would have helped">market never offered it</td>
                  <td>{c.neverOffered}</td>
                </tr>
                <tr>
                  <td className="muted" title="the debit beat the credit but not fee_buffer — our price gate cost us the fly">blocked by fee_buffer</td>
                  <td>{c.bufferBlocked}</td>
                </tr>
                <tr>
                  <td className="muted" title="cleared the buffer but the post-fee floor missed min_floor_dollars — read from the decisions journal">blocked by min_floor_dollars</td>
                  <td>{c.floorBlocked}</td>
                </tr>
                <tr><td className="muted">never priced</td><td>{c.unknown}</td></tr>
              </tbody>
            </table>
          )}
        </section>

        {lvp !== null && (
          <section className="card">
            <h2>
              Live vs paper — {lvp.arm} arm (contemporaneous){" "}
              {lvp.abort.triggered ? (
                <span className="chain-badge chain-badge-short">ABORT RULE TRIGGERED</span>
              ) : lvp.abort.armed ? (
                <span className="chain-badge">abort rule armed</span>
              ) : (
                <span className="chain-badge">{lvp.live.entries}/{lvp.abort.minLiveEntries} entries to arm abort rule</span>
              )}
            </h2>
            <table className="data-table">
              <thead>
                <tr><th></th><th>live</th><th>paper (same sessions)</th></tr>
              </thead>
              <tbody>
                <tr><td className="muted">entries</td><td>{lvp.live.entries}</td><td>{lvp.paper.entries}</td></tr>
                <tr><td className="muted">completed</td><td>{lvp.live.completed}</td><td>{lvp.paper.completed}</td></tr>
                <tr>
                  <td className="muted">completion rate</td>
                  <td>{lvp.live.completionRatePct !== null ? `${lvp.live.completionRatePct.toFixed(0)}%` : "—"}</td>
                  <td>{lvp.paper.completionRatePct !== null ? `${lvp.paper.completionRatePct.toFixed(0)}%` : "—"}</td>
                </tr>
                <tr>
                  <td className="muted">median latency</td>
                  <td>{lvp.live.medianLatencyMin !== null ? `${lvp.live.medianLatencyMin.toFixed(0)}m` : "—"}</td>
                  <td>{lvp.paper.medianLatencyMin !== null ? `${lvp.paper.medianLatencyMin.toFixed(0)}m` : "—"}</td>
                </tr>
                <tr>
                  <td className="muted">avg credit</td>
                  <td>{lvp.live.avgCredit !== null ? lvp.live.avgCredit.toFixed(2) : "—"}</td>
                  <td>{lvp.paper.avgCredit !== null ? lvp.paper.avgCredit.toFixed(2) : "—"}</td>
                </tr>
                <tr>
                  <td className="muted">completion gap</td>
                  <td colSpan={2} className={lvp.completionGapPct !== null && lvp.completionGapPct > lvp.abort.gapLimitPct ? "pnl-neg" : ""}>
                    {lvp.completionGapPct !== null ? `${lvp.completionGapPct.toFixed(1)}pp (halt if > ${lvp.abort.gapLimitPct.toFixed(0)}pp with ≥${lvp.abort.minLiveEntries} live entries)` : "—"}
                  </td>
                </tr>
              </tbody>
            </table>
          </section>
        )}
      </div>

      <section className="card">
        <h2>Completion rate by session</h2>
        {isLoading ? <span className="skeleton skeleton-text" style={{ width: "40%" }} /> : <CompletionTrend trend={data?.completionTrend ?? []} />}
      </section>
    </div>
  );
}
