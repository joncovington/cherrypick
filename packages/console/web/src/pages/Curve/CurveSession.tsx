import type { CurvePayload } from "@console/shared";
import { Card } from "../../components/DataTable";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { GridCard, StatTile } from "../../components/grid/GridCard";
import { fmtMoney, fmtNum } from "../../lib/format";
import { TimeLineChart } from "../../components/chart/TimeLineChart";
import { SERIES_COLORS } from "../../components/Charts";
import { useCurveEquity } from "../../lib/api";

/**
 * curve's session tab: the resolved session (`data.session`) as tiles and shapes, every card linking
 * to the page in the rail that explains it -- nothing opens an overlay. The day's regime read is
 * recorded every session whether or not anything traded, so it leads: it is the module's second
 * product, not a footnote to the first. Tones are signs, never verdicts.
 */
export function CurveSession({ data, loading }: { data: CurvePayload | undefined; loading: boolean }) {
  if (data !== undefined && !data.dbPresent) {
    return (
      <div className="cards cards-wide">
        <Card title="curve" collapseKey="curve-absent">
          <p className="muted">
            This module has not run on this machine -- there is no paper store at{" "}
            <span className="mono">~/.cherrypick/data/curve/paper_trades.db</span> yet. The page fills in once its first
            scheduled session runs.
          </p>
        </Card>
      </div>
    );
  }

  const today = data?.regimeSeries.find((r) => r.tradeDate === data.session);
  const open = data?.openPositions ?? [];
  const marked = open.filter((p) => p.unrealisedNet !== null);
  const openNet = marked.reduce((n, p) => n + (p.unrealisedNet ?? 0), 0);

  const byArm = new Map<string, { net: number; positions: number }>();
  for (const c of data?.arms ?? []) {
    const t = byArm.get(c.arm) ?? { net: 0, positions: 0 };
    t.net += c.netPnl ?? 0;
    t.positions += c.positions;
    byArm.set(c.arm, t);
  }
  const armRows = [...byArm.entries()].sort((a, b) => b[1].net - a[1].net);
  const closedNet = armRows.reduce((n, [, t]) => n + t.net, 0);
  const closedPositions = armRows.reduce((n, [, t]) => n + t.positions, 0);
  const recent = (data?.regimeSeries ?? []).filter((r) => r.ratio !== null).slice(-10);
  const equity = useCurveEquity();
  const equityLines = Object.entries(equity.data?.arms ?? {})
    .filter(([, e]) => e.series.length > 1)
    .map(([arm, e], i) => ({
      label: arm,
      color: SERIES_COLORS[i % SERIES_COLORS.length],
      points: e.series.map(([x, y]) => ({ x, y })),
    }));

  return (
    <div className="grid-12">
      <StatTile
        label="regime today"
        value={today?.ratio != null ? fmtNum(today.ratio, 3) : null}
        tone="dim"
        to="/curve/regime"
        toLabel="the daily VIX/VIX3M regime read"
        foot={
          today === undefined
            ? "no read recorded for this session"
            : today.usable
              ? `${today.regime ?? "—"}${today.hook ? " · hook" : ""} · VIX/VIX3M`
              : `refused: ${today.refusal ?? "unusable"}`
        }
      />
      <StatTile
        label="open positions"
        value={data === undefined ? null : String(data.openCount)}
        to="/curve/positions"
        toLabel="every open position, marked"
        foot={
          data === undefined
            ? "—"
            : open.length === 0
              ? "nothing open"
              : marked.length === 0
                ? "no usable mark yet"
              : `marked net ${fmtMoney(openNet)} · costs to date`
        }
      />
      <StatTile
        label="closed net"
        value={data === undefined || closedPositions === 0 ? null : fmtMoney(closedNet)}
        tone={closedPositions === 0 ? "dim" : closedNet >= 0 ? "pos" : "neg"}
        to="/curve/history"
        toLabel="every completed cycle"
        foot={closedPositions === 0 ? "no cycle has completed yet" : `${String(closedPositions)} completed cycles · after every cost`}
      />
      <StatTile
        label="flip divergence"
        value={data === undefined ? null : String(data.flipDivergence.flipDivergenceCount)}
        tone="dim"
        to="/curve/arms"
        toLabel="the noflip comparison"
        foot={
          data === undefined
            ? "—"
            : `noflip's effective sample · ${String(data.flipDivergence.controlFlipExits)} control flip exits`
        }
      />

      <GridCard
        label="marked equity by arm"
        span={6}
        h={304}
        to="/curve/performance"
        toLabel="the marked equity, its drawdowns and the closed-trade metrics"
        foot={`open spreads at their marks, after every cost · closed net ${armRows.length === 0 ? "—" : armRows.map(([arm, t]) => `${arm} ${fmtMoney(t.net)}`).join(", ")}`}
      >
        {equityLines.length === 0 ? (
          <p className="muted">{loading || equity.isLoading ? "reading…" : "not enough sessions yet"}</p>
        ) : (
          <TimeLineChart series={equityLines} height={240} />
        )}
      </GridCard>

      <GridCard
        label="VIX/VIX3M, last 10 sessions"
        span={6}
        h={304}
        to="/curve/regime"
        toLabel="the full regime series"
        foot="the ratio the entry gate reads, drawn from 1 · left of centre is contango"
      >
        <DivergingBars
          rows={recent.map((r) => ({ label: r.tradeDate, value: r.ratio === null ? null : r.ratio - 1, title: r.regime ?? undefined }))}
          format={(v) => fmtNum(v + 1, 3)}
          tone="none"
          emptyText={loading ? "reading…" : "no regime read recorded yet"}
        />
      </GridCard>
    </div>
  );
}
