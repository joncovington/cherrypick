import type { PmccPayload } from "@console/shared";
import { Card } from "../../components/DataTable";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { GridCard, StatTile } from "../../components/grid/GridCard";
import { fmtMoney } from "../../lib/format";

/**
 * PMCC-99's session tab: the resolved session (`data.session`) and the book, as tiles and shapes
 * whose every card links to the page in the rail that explains it -- nothing opens an overlay. The
 * header's symbol filter scopes it, as it scopes every page. Tones are signs, never verdicts.
 */
export function PmccSession({
  data,
  loading,
  symbol,
}: {
  data: PmccPayload | undefined;
  loading: boolean;
  symbol: string | null;
}) {
  if (data !== undefined && !data.dbPresent) {
    return (
      <div className="cards cards-wide">
        <Card title="PMCC-99" collapseKey="pmcc-absent">
          <p className="muted">
            This module has not run on this machine — there is no paper store at{" "}
            <span className="mono">~/.cherrypick/data/pmcc/paper_trades.db</span> yet. Nothing is wrong; the page
            fills in after its first session.
          </p>
        </Card>
      </div>
    );
  }

  const inScope = <T extends { symbol: string | null }>(r: T) => symbol === null || r.symbol === null || r.symbol === symbol;
  const open = (data?.openPositions ?? []).filter(inScope);
  const marked = open.filter((p) => p.unrealisedNet !== null);
  const openNet = marked.reduce((n, p) => n + (p.unrealisedNet ?? 0), 0);

  const byArm = new Map<string, { net: number; positions: number }>();
  for (const c of (data?.arms ?? []).filter(inScope)) {
    const t = byArm.get(c.arm) ?? { net: 0, positions: 0 };
    t.net += c.netPnl ?? 0;
    t.positions += c.positions;
    byArm.set(c.arm, t);
  }
  const armRows = [...byArm.entries()].sort((a, b) => b[1].net - a[1].net);
  const closedNet = armRows.reduce((n, [, t]) => n + t.net, 0);
  const closedPositions = armRows.reduce((n, [, t]) => n + t.positions, 0);

  const attempts = (data?.today.attempts ?? []).filter(inScope);
  const filled = attempts.filter((a) => a.outcome === "filled").reduce((n, a) => n + a.n, 0);
  const events = (data?.today.events ?? []).filter(inScope);
  const byAction = new Map<string, number>();
  for (const e of events) byAction.set(e.action, (byAction.get(e.action) ?? 0) + e.n);
  const held = events.filter((e) => !e.executed).reduce((n, e) => n + e.n, 0);

  return (
    <div className="grid-12">
      <StatTile
        label="open positions"
        value={data === undefined ? null : String(open.length)}
        to="/pmcc/positions"
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
        tone={closedNet >= 0 ? "pos" : "neg"}
        to="/pmcc/history"
        toLabel="every completed cycle"
        foot={closedPositions === 0 ? "no cycle has completed yet" : `${String(closedPositions)} completed cycles · after every cost`}
      />
      <StatTile
        label="entries this session"
        value={data === undefined ? null : String(filled)}
        to="/pmcc/decisions"
        toLabel="this session's attempts and verdicts"
        foot={data?.session == null ? "—" : `${data.session} · ${String(attempts.reduce((n, a) => n + a.n, 0))} evaluations`}
      />
      <StatTile
        label="management verdicts"
        value={data === undefined ? null : String(events.reduce((n, e) => n + e.n, 0))}
        tone="dim"
        to="/pmcc/decisions"
        toLabel="this session's management log"
        foot={held > 0 ? `${String(held)} seen and held by a gate` : "every verdict reached was acted on"}
      />

      <GridCard
        label="closed net by arm"
        span={6}
        h={304}
        to="/pmcc/arms"
        toLabel="the arm comparison"
        foot="after every cost"
      >
        <DivergingBars
          rows={armRows.map(([arm, t]) => ({ label: arm, value: t.net, title: `${arm}: ${String(t.positions)} cycles` }))}
          format={fmtMoney}
          emptyText={loading ? "reading…" : "no cycle has completed yet"}
        />
      </GridCard>

      <GridCard
        label="verdicts this session"
        span={6}
        h={304}
        to="/pmcc/decisions"
        toLabel="the management log"
        foot="management verdicts by action, executed or held"
      >
        <DivergingBars
          rows={[...byAction.entries()].sort((a, b) => b[1] - a[1]).map(([action, n]) => ({ label: action, value: n }))}
          format={(v) => v.toLocaleString()}
          tone="none"
          emptyText={loading ? "reading…" : "no management verdicts recorded on this session"}
        />
      </GridCard>
    </div>
  );
}
