import { BarChart } from "../../components/Charts";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { GridCard, StatTile } from "../../components/grid/GridCard";
import { fmtMoney } from "../../lib/format";

export interface EarningsAnalytics {
  kpis: { totalNet: number; closedTrades: number; expectancy: number | null; strategiesActive: number };
  openPositions: Array<{
    strategy: string;
    symbol: string;
    quantity: number | null;
    price: number | null;
    credit: number | null;
    netOfCost: number | null;
    maxLoss: number | null;
    entryCost: number | null;
    expiration: string | null;
  }>;
  weekly: Array<{ week: string; net: number }>;
  strategies: Array<{ strategy: string; trades: number; winRatePct: number | null; profitFactor: number | null; expectancy: number | null; net: number }>;
}

/**
 * earnings' session tab: where the book stands, as tiles and shapes whose every card links to the
 * page in the rail that explains it -- nothing opens an overlay. earnings holds positions across
 * sessions (a winner up to three, a loser closes on the first morning), so "session" here is the
 * book as of now rather than one day's entries; the era select in the header scopes the closed
 * figures. Tones are signs, never verdicts.
 */
export function EarningsSession({
  analytics,
  loading,
  recommended,
}: {
  analytics: EarningsAnalytics | undefined;
  loading: boolean;
  recommended: number | null;
}) {
  const a = analytics;
  const open = a?.openPositions ?? [];
  const atRisk = open.reduce((s, p) => s + Math.abs(p.maxLoss ?? 0), 0);

  return (
    <div className="grid-12">
      <StatTile
        label="open positions"
        value={a === undefined ? null : String(open.length)}
        to="/earnings/positions"
        toLabel="every open position, marked"
        foot={a === undefined ? "—" : open.length === 0 ? "nothing carrying risk" : `${fmtMoney(atRisk)} at risk`}
      />
      <StatTile
        label="net this era"
        value={a === undefined ? null : fmtMoney(a.kpis.totalNet)}
        tone={(a?.kpis.totalNet ?? 0) >= 0 ? "pos" : "neg"}
        to="/earnings/history"
        toLabel="every closed trade"
        foot={a === undefined ? "—" : `${String(a.kpis.closedTrades)} closed trades · after every cost`}
      />
      <StatTile
        label="expectancy / trade"
        value={a?.kpis.expectancy != null ? fmtMoney(a.kpis.expectancy) : null}
        tone={(a?.kpis.expectancy ?? 0) >= 0 ? "pos" : "neg"}
        to="/earnings/strategies"
        toLabel="the cross-strategy comparison"
        foot={a === undefined ? "—" : `net of costs · ${String(a.kpis.strategiesActive)} strategies active`}
      />
      <StatTile
        label="recommended ahead"
        value={recommended === null ? null : String(recommended)}
        tone="dim"
        to="/earnings/upcoming"
        toLabel="the forward earnings scan"
        foot="symbols the forward scan tiers as recommended"
      />

      <GridCard
        label="weekly net"
        span={8}
        h={304}
        to="/earnings/history"
        toLabel="the closed trades behind weekly net"
        foot={a === undefined ? (loading ? "reading…" : "—") : `last ${String(Math.min(16, a.weekly.length))} weeks · net of costs`}
      >
        {a !== undefined && a.weekly.length > 0 ? (
          <BarChart bars={a.weekly.slice(-16).map((w) => ({ x: w.week, y: w.net }))} height={220} />
        ) : (
          <p className="muted">{loading ? "reading…" : "nothing closed in this era yet"}</p>
        )}
      </GridCard>

      <GridCard
        label="net by strategy"
        span={4}
        h={304}
        to="/earnings/strategies"
        toLabel="per-strategy detail"
        foot="net of costs · this era"
      >
        <DivergingBars
          rows={(a?.strategies ?? []).map((s) => ({ label: s.strategy, value: s.net, title: `${s.strategy}: ${String(s.trades)} trades` }))}
          format={fmtMoney}
          emptyText={loading ? "reading…" : "nothing closed in this era yet"}
        />
      </GridCard>
    </div>
  );
}
