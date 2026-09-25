import type { CalendarsPayload } from "@console/shared";
import { Card } from "../../components/DataTable";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { GridCard, StatTile } from "../../components/grid/GridCard";
import { fmtMoney } from "../../lib/format";

/**
 * calendars' session tab: this week's structures and the book, as tiles and shapes whose every
 * card links to the page in the rail that explains it -- nothing opens an overlay. Every arm enters
 * from the same plan, so net by arm is exit policy and nothing else. Tones are signs, never verdicts.
 */
export function CalendarsSession({ data, loading }: { data: CalendarsPayload | undefined; loading: boolean }) {
  if (data !== undefined && !data.dbPresent) {
    return (
      <div className="cards cards-wide">
        <Card title="Calendars" collapseKey="cal-absent">
          <p className="muted">
            This module has not run on this machine — there is no paper store at{" "}
            <span className="mono">~/.cherrypick/data/calendars/paper_trades.db</span> yet. Nothing is wrong; the page
            fills in after its first session.
          </p>
        </Card>
      </div>
    );
  }

  const week = data?.currentWeek.positions ?? [];
  const open = data?.openPositions ?? [];
  const marked = open.filter((p) => p.unrealisedNet !== null);
  const openNet = marked.reduce((n, p) => n + (p.unrealisedNet ?? 0), 0);

  const byArm = new Map<string, { net: number; weeks: number }>();
  for (const c of data?.arms ?? []) {
    const t = byArm.get(c.arm) ?? { net: 0, weeks: 0 };
    t.net += c.netPnl ?? 0;
    t.weeks += c.weeks;
    byArm.set(c.arm, t);
  }
  const armRows = [...byArm.entries()].sort((a, b) => b[1].net - a[1].net);
  const closedNet = armRows.reduce((n, [, t]) => n + t.net, 0);
  const anyClosed = armRows.some(([, t]) => t.weeks > 0);

  const decisions = data?.today.decisions ?? [];
  const byReason = new Map<string, number>();
  for (const d of decisions) byReason.set(d.reason, (byReason.get(d.reason) ?? 0) + d.occurrences);

  return (
    <div className="grid-12">
      <StatTile
        label="this week"
        value={data === undefined ? null : String(week.length)}
        to="/calendars/positions"
        toLabel="this week's structures and anything carried"
        foot={data?.currentWeek.weekOf == null ? "no week entered yet" : `structures for the week of ${data.currentWeek.weekOf}`}
      />
      <StatTile
        label="open positions"
        value={data === undefined ? null : String(open.length)}
        to="/calendars/positions"
        toLabel="every open position, marked"
        foot={
          open.length === 0 ? "nothing open" : marked.length === 0 ? "no usable mark yet" : `marked net ${fmtMoney(openNet)} · costs to date`
        }
      />
      <StatTile
        label="closed net"
        value={data === undefined || !anyClosed ? null : fmtMoney(closedNet)}
        tone={closedNet >= 0 ? "pos" : "neg"}
        to="/calendars/history"
        toLabel="every week, per arm"
        foot={anyClosed ? "finished weeks · after every cost" : "no week has finished yet"}
      />
      <StatTile
        label="decisions today"
        value={data === undefined ? null : decisions.reduce((n, d) => n + d.occurrences, 0).toLocaleString()}
        tone="dim"
        to="/calendars/decisions"
        toLabel="today's decision journal"
        foot={decisions.some((d) => d.accepted) ? "an entry was accepted" : "no entry accepted on this session"}
      />

      <GridCard
        label="closed net by arm"
        span={6}
        h={304}
        to="/calendars/arms"
        toLabel="the arm comparison"
        foot="finished weeks · same entry plan, so the difference is exit policy"
      >
        <DivergingBars
          rows={armRows.map(([arm, t]) => ({ label: arm, value: t.net, title: `${arm}: ${String(t.weeks)} weeks` }))}
          format={fmtMoney}
          emptyText={loading ? "reading…" : "no week has finished yet"}
        />
      </GridCard>

      <GridCard
        label="why today went the way it did"
        span={6}
        h={304}
        to="/calendars/decisions"
        toLabel="today's decision journal"
        foot="the collapsed journal: a gate that blocks all morning is one bar with a count"
      >
        <DivergingBars
          rows={[...byReason.entries()].sort((a, b) => b[1] - a[1]).map(([reason, n]) => ({ label: reason, value: n }))}
          format={(v) => v.toLocaleString()}
          tone="none"
          emptyText={loading ? "reading…" : "the journal recorded nothing on this session"}
        />
      </GridCard>
    </div>
  );
}
