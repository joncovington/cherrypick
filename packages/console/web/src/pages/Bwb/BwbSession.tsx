import type { BwbPayload } from "@console/shared";
import { Card } from "../../components/DataTable";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { GridCard, StatTile } from "../../components/grid/GridCard";
import { fmtMoney } from "../../lib/format";

/**
 * bwb's session tab: the resolved session (`data.session`) and the ladder it is part of, as tiles
 * and shapes whose every card links to the page in the rail that explains it — nothing opens an
 * overlay. The dense tables (open trades, completed positions, the arm comparison with its caveats)
 * are pages of their own.
 *
 * Tones are signs, never verdicts. bwb's own honesty rule rides the fire-rate card: until an arm's
 * add-on fires, its positions are byte-identical to control's, so the effective sample for an
 * arm-vs-control comparison is the fire count, not the trade count.
 */
export function BwbSession({ data, loading }: { data: BwbPayload | undefined; loading: boolean }) {
  if (data !== undefined && !data.dbPresent) {
    return (
      <div className="cards cards-wide">
        <Card title="bwb" collapseKey="bwb-absent">
          <p className="muted">
            This module has not run on this machine -- there is no paper store at{" "}
            <span className="mono">~/.cherrypick/data/bwb/paper_trades.db</span> yet. The page fills in once its first
            scheduled session runs.
          </p>
        </Card>
      </div>
    );
  }

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

  const fires = (data?.fireCounts ?? []).filter((c) => c.arm !== "control");
  const fired = fires.reduce((n, c) => n + c.fired, 0);
  const firePositions = fires.reduce((n, c) => n + c.positions, 0);

  const attempts = data?.entryAttemptsToday ?? [];
  const filled = attempts.filter((a) => a.outcome === "filled").length;

  return (
    <div className="grid-12">
      <StatTile
        label="open positions"
        value={data === undefined ? null : String(data.openCount)}
        to="/bwb/positions"
        toLabel="every open position, marked"
        foot={
          data === undefined
            ? "—"
            : open.length === 0
              ? "nothing open"
              : marked.length === 0
                ? "no usable mark yet"
              : `marked net ${fmtMoney(openNet)} · ${String(marked.length)} of ${String(open.length)} marked · costs to date`
        }
      />
      <StatTile
        label="closed net"
        value={data === undefined ? null : closedPositions === 0 ? null : fmtMoney(closedNet)}
        tone={closedNet >= 0 ? "pos" : "neg"}
        to="/bwb/history"
        toLabel="every completed position"
        foot={closedPositions === 0 ? "nothing has settled yet" : `${String(closedPositions)} completed positions · after every cost`}
      />
      <StatTile
        label="add-ons fired"
        value={data === undefined ? null : String(fired)}
        tone="dim"
        to="/bwb/arms"
        toLabel="fire counts and net by arm"
        foot={`of ${String(firePositions)} positions on the trigger arms`}
      />
      <StatTile
        label="entries this session"
        value={data === undefined ? null : String(filled)}
        to="/bwb/decisions"
        toLabel="this session's decisions"
        foot={data?.session == null ? "—" : `${data.session} · ${String(attempts.length)} attempts`}
      />

      <GridCard
        label="closed net by arm"
        span={6}
        h={304}
        to="/bwb/history"
        toLabel="the completed positions behind net by arm"
        foot="after every cost · concurrent positions share regime context, so rows are not independent samples"
      >
        <DivergingBars
          rows={armRows.map(([arm, t]) => ({ label: arm, value: t.net, title: `${arm}: ${String(t.positions)} positions` }))}
          format={fmtMoney}
          emptyText={loading ? "reading…" : "nothing has settled yet"}
        />
      </GridCard>

      <GridCard
        label="add-on fire rate by arm"
        span={6}
        h={304}
        to="/bwb/arms"
        toLabel="fire counts and the arm comparison"
        foot="the effective sample per arm is the fire count, not the trade count"
      >
        <DivergingBars
          rows={fires.map((c) => ({
            label: c.arm,
            value: c.fireRate === null ? null : c.fireRate * 100,
            title: `${c.arm}: ${String(c.fired)} of ${String(c.positions)}`,
          }))}
          format={(v) => `${v.toFixed(0)}%`}
          tone="none"
          emptyText={loading ? "reading…" : "no positions on a trigger arm yet"}
        />
      </GridCard>
    </div>
  );
}
