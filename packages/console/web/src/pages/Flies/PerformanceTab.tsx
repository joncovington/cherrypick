import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { LiveVsPaper, TradingMode } from "@console/shared";
import { fliesQuery, type FliesFilter } from "../../lib/api";
import { Card, fmtMoney } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { useOpenParam } from "../Backtest/CardLightbox";
import { FliesLayout } from "./FliesFilter";
import { Section } from "../../components/Section";
import { BoxOpen, FliesBoxHost, type FliesBox } from "./FliesBoxes";
import { EquityUnderwater } from "../../components/EquityUnderwater";
import { VoidedNote } from "./VoidedNote";
import { DailyRiskTiles, dailyRiskFlag, dailyRiskNote } from "../../components/performance/DailyRiskTiles";
import { TileGrid } from "../../components/performance/TileGrid";

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
    undersampledFlag: boolean;
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
  liveVsPaper: LiveVsPaper | null;
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

export function Tile({ label, value, tone, title }: { label: string; value: string; tone?: "pos" | "neg" | "dim"; title?: string }) {
  const cls = tone === "pos" ? "pnl-pos" : tone === "neg" ? "pnl-neg" : tone === "dim" ? "muted" : "";
  return (
    <div className="stat-tile" title={title}>
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${cls}`}>{value}</span>
    </div>
  );
}

/** A ratio, or an em-dash where it is undefined. Never 0 — see riskMetrics.ts on why. */
export function fmtRatio(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}

export function tone(v: number | null): "pos" | "neg" | "dim" | undefined {
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
      <line x1={m.l} y1={Y(0)} x2={width - m.r} y2={Y(0)} stroke="var(--chart-grid-strong)" />
      {[yMax, 0, yMin].map((v, i) => (
        <text key={i} x={4} y={Y(v) + 3} fontSize={9} fill="var(--text-muted)" fontFamily="Consolas, monospace">{fmtMoney(v)}</text>
      ))}
      {series.map((s, i) => (
        <rect
          key={s.bucket}
          x={m.l + i * bw + bw * 0.15}
          y={Math.min(Y(0), Y(s.netPnl))}
          width={Math.max(bw * 0.7, 1)}
          height={Math.max(Math.abs(Y(s.netPnl) - Y(0)), 1)}
          fill={s.netPnl >= 0 ? "var(--pos)" : "var(--neg)"}
        >
          <title>{`${s.bucket}: ${fmtMoney(s.netPnl)} (cum ${fmtMoney(s.cumulative)})`}</title>
        </rect>
      ))}
      {cumulative && (
        <polyline
          points={series.map((s, i) => `${(m.l + (i + 0.5) * bw).toFixed(1)},${Y(s.cumulative).toFixed(1)}`).join(" ")}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={1.6}
        />
      )}
      {series.length > 0 && (
        <>
          <text x={m.l} y={height - 6} fontSize={9} fill="var(--text-muted)" fontFamily="Consolas, monospace">{series[0]!.bucket}</text>
          <text x={width - m.r} y={height - 6} fontSize={9} fill="var(--text-muted)" textAnchor="end" fontFamily="Consolas, monospace">
            {series[series.length - 1]!.bucket}
          </text>
        </>
      )}
    </svg>
  );
}

/** Per-session completion rate — the number that decides whether the strategy is real, on a trend. */
export function CompletionTrend({ trend }: { trend: Performance["completionTrend"] }) {
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
          <line x1={m.l} y1={Y(p)} x2={width - m.r} y2={Y(p)} stroke="var(--chart-grid)" />
          <text x={4} y={Y(p) + 3} fontSize={9} fill="var(--text-muted)" fontFamily="Consolas, monospace">{p}%</text>
        </g>
      ))}
      <polyline
        points={trend.filter((t) => t.ratePct !== null).map((t, i) => `${X(i).toFixed(1)},${Y(t.ratePct!).toFixed(1)}`).join(" ")}
        fill="none"
        stroke="var(--pos)"
        strokeWidth={1.6}
      />
      {trend.map((t, i) =>
        t.ratePct !== null ? (
          <circle key={t.day} cx={X(i)} cy={Y(t.ratePct)} r={2.5} fill="var(--pos)">
            <title>{`${t.day}: ${t.completed}/${t.legged} (${t.ratePct.toFixed(0)}%)`}</title>
          </circle>
        ) : null,
      )}
      <text x={m.l} y={height - 4} fontSize={9} fill="var(--text-muted)" fontFamily="Consolas, monospace">{trend[0]!.day}</text>
      <text x={width - m.r} y={height - 4} fontSize={9} fill="var(--text-muted)" textAnchor="end" fontFamily="Consolas, monospace">{trend[trend.length - 1]!.day}</text>
    </svg>
  );
}

// The cards below are shared with the Live page, which carries this tab's live-mode figures for
// the pilot's arm: one rendering, so the two pages cannot word the same number differently. The
// tab itself (2026-11-07) shows the same tables as the tabs of one lightbox, built from the same
// bodies.
type Completion = Performance["completion"];
type Roll = NonNullable<Performance["roll"]>;
type LeftOnTable = NonNullable<Performance["leftOnTable"]>;
type Lvp = NonNullable<Performance["liveVsPaper"]>;

function CompletionStats({ c }: { c: Completion }) {
  return (
    <div className="table-scroll">
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
    </div>
  );
}

/** Why the legged entries that did not complete did not: opposite remedies, so never lumped. */
function MissesTable({ c }: { c: Completion }) {
  return (
    <div className="table-scroll">
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
    </div>
  );
}

export function CompletionCard({ c }: { c: Completion | undefined }) {
  return (
    <section className="card">
      <h2>Completion (legged — the number that decides if this is real)</h2>
      {c !== undefined && (
        <>
          <CompletionStats c={c} />
          {/* The misses are the shortfall above (legged − completed), so they sit in this card rather
              than a short one beside it that left half a row blank. */}
          <h3 className="card-subhead">Why misses missed (opposite remedies — do not lump)</h3>
          <MissesTable c={c} />
        </>
      )}
    </section>
  );
}

function AbortBadge({ lvp }: { lvp: Lvp }) {
  return lvp.paper === null ? (
    <span className="chain-badge">abort rule unavailable</span>
  ) : lvp.abort.triggered ? (
    <span className="chain-badge chain-badge-short">ABORT RULE TRIGGERED</span>
  ) : lvp.abort.armed ? (
    <span className="chain-badge">abort rule armed</span>
  ) : (
    <span className="chain-badge">{lvp.live.entries}/{lvp.abort.minLiveEntries} entries to arm abort rule</span>
  );
}

/** The halt rule's own words, as the pilot reads them. */
export function haltRule(lvp: Lvp): string {
  return `Halts the pilot if the completion gap exceeds ${lvp.abort.gapLimitPct.toFixed(0)}pp with at least ${String(lvp.abort.minLiveEntries)} live entries.`;
}

function LiveVsPaperBody({ lvp, rule = true }: { lvp: Lvp; rule?: boolean }) {
  // No paper side means no gap and no verdict: the rule is unavailable, never "not triggered".
  const paper = lvp.paper;
  const pct = (v: number | null | undefined) => (v !== null && v !== undefined ? `${v.toFixed(0)}%` : "—");
  const mins = (v: number | null | undefined) => (v !== null && v !== undefined ? `${v.toFixed(0)}m` : "—");
  return (
    <>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr><th></th><th>live</th><th>paper (same sessions)</th></tr>
          </thead>
          <tbody>
            <tr><td className="muted">entries</td><td>{lvp.live.entries}</td><td>{paper?.entries ?? "—"}</td></tr>
            <tr><td className="muted">completed</td><td>{lvp.live.completed}</td><td>{paper?.completed ?? "—"}</td></tr>
            <tr>
              <td className="muted">completion rate</td>
              <td>{pct(lvp.live.completionRatePct)}</td>
              <td>{pct(paper?.completionRatePct)}</td>
            </tr>
            <tr>
              <td className="muted">median latency</td>
              <td>{mins(lvp.live.medianLatencyMin)}</td>
              <td>{mins(paper?.medianLatencyMin)}</td>
            </tr>
            <tr>
              <td className="muted">avg credit</td>
              <td>{lvp.live.avgCredit !== null ? lvp.live.avgCredit.toFixed(2) : "—"}</td>
              <td>{paper !== null && paper.avgCredit !== null ? paper.avgCredit.toFixed(2) : "—"}</td>
            </tr>
            <tr>
              <td className="muted">completion gap</td>
              <td colSpan={2} className={lvp.completionGapPct !== null && lvp.completionGapPct > lvp.abort.gapLimitPct ? "pnl-neg" : ""}>
                {lvp.completionGapPct !== null ? `${lvp.completionGapPct.toFixed(1)}pp` : paper === null ? "unavailable" : "—"}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      {paper === null && (
        <p className="muted" style={{ fontSize: 12, margin: "0.4rem 0 0" }}>
          No paper figures to compare against ({lvp.degraded?.reason ?? "paper ledger unreadable"}), so the gap and the
          abort verdict cannot be evaluated.
        </p>
      )}
      {/* The rule is prose and `.data-table td` is nowrap by house rule: inside the cell it pushed
          the table past the card's right edge. */}
      {rule && (
        <p className="muted" style={{ fontSize: 12, margin: "0.4rem 0 0" }}>
          {haltRule(lvp)}
        </p>
      )}
    </>
  );
}

export function LiveVsPaperCard({ lvp }: { lvp: Lvp }) {
  return (
    <section className="card">
      <h2>
        Live vs paper — {lvp.arm} arm (contemporaneous) <AbortBadge lvp={lvp} />
      </h2>
      <LiveVsPaperBody lvp={lvp} />
    </section>
  );
}

function RollBody({ roll }: { roll: Roll }) {
  return (
    <>
      <div className="table-scroll">
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
      </div>
      <p className="muted" style={{ fontSize: 12, margin: "0.4rem 0 0" }}>
        Until it rolls, a bwb carries real negative tail risk of <strong>wing − far wing</strong> — unlike an uncompleted legged entry,
        whose worst case is its own defined width. An unrolled bwb is not a neutral outcome.
      </p>
      {roll.leggedEntries > 0 && roll.leggedEntries < 5 && (
        <p className="muted" style={{ fontSize: 12, margin: "0.3rem 0 0" }}>
          <strong>{roll.leggedEntries} entries</strong> — too few to read as a rate.
        </p>
      )}
      <h3 className="card-subhead">Why rolls missed</h3>
      <div className="table-scroll">
        <table className="data-table">
          <tbody>
            <tr><td className="muted">never cheap enough</td><td>{roll.neverOffered}</td></tr>
            <tr><td className="muted">our buffer refused it</td><td>{roll.bufferBlocked}</td></tr>
            <tr><td className="muted">our floor refused it</td><td>{roll.floorBlocked}</td></tr>
            <tr><td className="muted">unknown</td><td>{roll.unknown}</td></tr>
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 12, margin: "0.4rem 0 0" }}>
        Read against <code>best_roll_debit</code>: &quot;the market never made the roll cheap enough&quot; and &quot;our own gate refused a
        roll that was offered&quot; are identical in the P&amp;L and call for opposite fixes.
      </p>
    </>
  );
}

function LeftOnTableBody({ lot }: { lot: LeftOnTable }) {
  return (
    <>
      <div className="table-scroll">
        <table className="data-table">
          <tbody>
            <tr><td className="muted">completions tracked</td><td>{lot.overall.n}</td></tr>
            <tr><td className="muted">improved by waiting</td><td>{lot.overall.improved}</td></tr>
            <tr><td className="muted">median improvement</td><td>{lot.overall.medianImprovementDollars !== null ? fmtMoney(lot.overall.medianImprovementDollars) : "—"}</td></tr>
            <tr><td className="muted">total left on table</td><td>{lot.overall.totalImprovementDollars !== null ? fmtMoney(lot.overall.totalImprovementDollars) : "—"}</td></tr>
            <tr><td className="muted">untracked (pre-2026-08-03, or iron/bwb)</td><td className="muted">{lot.untracked}</td></tr>
          </tbody>
        </table>
      </div>
      {Object.keys(lot.byGexBucket).length > 0 && (
        <div className="table-scroll">
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
        </div>
      )}
      <p className="muted" style={{ fontSize: 12, margin: "0.4rem 0 0" }}>
        Improvement is floored at 0 per position — the completion tick itself seeds the tracker, so a price that never got better records as
        no improvement, not a loss. If pinning (positive gamma) shows the largest improvement here, that is the evidence waiting pays for
        debit-first; if the split shows no conditional difference, first-tick completion already captures it.
      </p>
    </>
  );
}

const PERFORMANCE_HELP: CardHelpContent = {
  title: "Performance",
  body: (
    <HelpSection title="The tiles">
      <p>Net P&amp;L is net of fees. Win rate and profit factor are over settled trades; completion is the legged entries that became flies.</p>
    </HelpSection>
  ),
};

const OVER_TIME_HELP: CardHelpContent = {
  title: "P&L over time",
  body: (
    <HelpSection title="Bars and line">
      <p>Each period&apos;s net as a bar, daily, weekly or monthly; the line is the cumulative.</p>
    </HelpSection>
  ),
};

const EQUITY_HELP: CardHelpContent = {
  title: "Equity and drawdown",
  body: (
    <HelpWarn title="1-lot samples">
      <p>
        Not a sized book: the base under the curve is a drawing constant, these arms take one-lot entries and do not compound, and this is
        the most authoritative-looking chart on the page. The drawdown is real. Always daily, whatever granularity is picked.
      </p>
    </HelpWarn>
  ),
};

/** The completion card's ?: the halt rule leads, as a warning, when the pilot has one to read. */
export function completionHelp(lvp: Lvp | null): CardHelpContent {
  return {
    title: "Completion",
    body: (
      <>
        {lvp !== null && (
          <HelpWarn title="The halt rule">
            <p>{haltRule(lvp)}</p>
          </HelpWarn>
        )}
        <HelpSection title="The number that decides">
          <p>
            Completion is the legged entries that became flies, the number that decides whether this is real. The misses are the shortfall
            between legged and completed; open the detail for why each missed.
          </p>
        </HelpSection>
      </>
    ),
  };
}

type CompTab = "missed" | "lvp" | "roll" | "left" | "sessions";

export function PerformanceTab({ mode, filter }: { mode: TradingMode; filter: FliesFilter }) {
  const [granularity, setGranularity] = useState<(typeof GRANULARITIES)[number]>("daily");
  const [cumulative, setCumulative] = useState(true);
  const lb = useOpenParam("comp");
  // `date` dropped: an equity curve pinned to one day is a point.
  const { data, isLoading } = usePerformance(mode, granularity, filter);
  const t = data?.tiles;
  const c = data?.completion;
  const roll = data?.roll ?? null;
  const lvp = data?.liveVsPaper ?? null;
  const lot = data?.leftOnTable ?? null;

  // The bwb arm has no completion rate: it is entered WHOLE for a credit and converted by a ROLL, not
  // legged in and completed, so its roll is a tab of its own, hidden when no bwb entries are in scope.
  // Left on table is debit_first's wait-for-better hypothesis, hidden when the scope holds no
  // debit_first completions.
  const tabs: Array<{ id: CompTab; label: string }> = [{ id: "missed", label: "why misses missed" }];
  if (lvp !== null) tabs.push({ id: "lvp", label: "live vs paper" });
  if (roll !== null) tabs.push({ id: "roll", label: "roll" });
  if (lot !== null) tabs.push({ id: "left", label: "left on table" });
  tabs.push({ id: "sessions", label: "by session" });
  const box: FliesBox = {
    id: "detail",
    kicker: "completion ›",
    title: "Completion detail",
    chips: lvp !== null ? <AbortBadge lvp={lvp} /> : undefined,
    tabs,
    body: (tab) => {
      switch (tab as CompTab) {
        case "lvp":
          return lvp !== null ? (
            <>
              <p className="muted pf-caption" style={{ marginTop: 0 }}>{lvp.arm} arm, contemporaneous</p>
              <LiveVsPaperBody lvp={lvp} />
            </>
          ) : null;
        case "roll":
          return roll !== null ? <RollBody roll={roll} /> : null;
        case "left":
          return lot !== null ? <LeftOnTableBody lot={lot} /> : null;
        case "sessions":
          return <CompletionTrend trend={data?.completionTrend ?? []} />;
        default:
          return c !== undefined ? (
            <>
              <p className="muted pf-caption" style={{ marginTop: 0 }}>Opposite remedies: do not lump them.</p>
              <MissesTable c={c} />
            </>
          ) : null;
      }
    },
  };

  return (
    <FliesLayout>
      <Section concept="stand" tag="the record" question="How has it done?">
        <Card title="performance" collapseKey="flies-performance" help={PERFORMANCE_HELP}>
          <TileGrid count={6}>
            <Tile label="net P&L" value={t !== undefined ? fmtMoney(t.netPnl) : "—"} tone={t !== undefined && t.netPnl >= 0 ? "pos" : "neg"} />
            <Tile label="trades" value={String(t?.trades ?? "—")} />
            <Tile label="win rate" value={t?.winRatePct != null ? `${t.winRatePct.toFixed(0)}%` : "—"} />
            <Tile label="profit factor" value={t?.profitFactor != null ? t.profitFactor.toFixed(2) : "—"} />
            <Tile label="fee drag" value={t?.feeDragPct != null ? `${t.feeDragPct.toFixed(1)}%` : "—"} tone="dim" />
            <Tile label="completion" value={t?.completionRatePct != null ? `${t.completionRatePct.toFixed(0)}%` : "—"} />
          </TileGrid>
          <VoidedNote mode={mode} filter={filter} />
        </Card>
      </Section>

      <Section concept="hist" tag="over time" question="How did it get here?">
        <Card
          title="P&L over time"
          collapseKey="flies-pnl-over-time"
          help={OVER_TIME_HELP}
          controls={
            <>
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
            </>
          }
        >
          {isLoading ? <span className="skeleton skeleton-text" style={{ width: "40%" }} /> : <PnlBars series={data?.series ?? []} cumulative={cumulative} />}
        </Card>

        {/* The same pair MEIC's performance tab carries, from the same shared module so the two
            cannot disagree about what a Sharpe is. Flagged "1-lot samples, not a sized book" for the
            reason MEIC's is: the base under the curve is a drawing constant, these arms take one-lot
            entries and do not compound, and this is the most authoritative-looking chart on the page.
            The drawdown is real. */}
        <Card
          title="equity and drawdown"
          collapseKey="flies-equity"
          help={{ title: EQUITY_HELP.title, body: <>{EQUITY_HELP.body}{dailyRiskNote(data?.risk)}</> }}
          flag={dailyRiskFlag(data?.risk) ?? "1-lot samples"}
        >
          {isLoading ? (
            <span className="skeleton skeleton-text" style={{ width: "40%" }} />
          ) : (data?.equity.length ?? 0) === 0 ? (
            <p className="muted">not enough history yet</p>
          ) : (
            <>
              <EquityUnderwater equity={data?.equity ?? []} />
              <div style={{ marginTop: "0.75rem" }}>
                <DailyRiskTiles risk={data?.risk} maxDrawdown={Math.max(...(data?.equity ?? []).map((e) => e.drawdown), 0)} />
              </div>
            </>
          )}
        </Card>
      </Section>

      <Section concept="edge" tag="completion" question="Is completion good enough to be real?">
        <Card title="completion" collapseKey="flies-completion" help={completionHelp(lvp)} flag={lvp !== null ? "halt rule" : undefined}>
          <TileGrid count={4}>
            <Tile label="legged" value={c !== undefined ? String(c.leggedEntries) : "—"} />
            <Tile label="completed" value={c !== undefined ? String(c.completed) : "—"} />
            <Tile label="rate" value={c?.completionRatePct != null ? `${c.completionRatePct.toFixed(0)}%` : "—"} />
            <Tile label="median minutes" value={c?.medianLatencyMin != null ? c.medianLatencyMin.toFixed(0) : "—"} />
          </TileGrid>
          {/* The abort verdict changes what a person would do, so it stays on the card. */}
          {lvp !== null && (
            <p className="sm">
              <AbortBadge lvp={lvp} />
              {lvp.completionGapPct !== null && <span className="muted"> completion gap to paper {lvp.completionGapPct.toFixed(1)}pp</span>}
            </p>
          )}
          <div className="pf-mores pf-push">
            {tabs.map((x) => (
              <BoxOpen key={x.id} lb={lb} id="detail" tab={x.id === "missed" ? undefined : x.id} hot={x.id === "lvp" && lvp !== null && lvp.abort.triggered}>
                {x.label}
              </BoxOpen>
            ))}
          </div>
        </Card>
      </Section>
      <FliesBoxHost lb={lb} boxes={[box]} />
    </FliesLayout>
  );
}
