import type { TradingMode } from "@console/shared";
import { useAttempts } from "../../components/Attempts";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { GridCard, StatTile } from "../../components/grid/GridCard";
import { useMeic } from "../../lib/api";
import { fmtMoney } from "../../lib/format";
import type { MeicScope } from "./MeicTables";

/**
 * MEIC's session tab: one session, the one the header's day select resolves to — the same rule
 * flies' session tab follows, and the same session every other card on the frame is showing.
 *
 * It used to open on calendar periods (today / week / month / all). After midnight "today" was an
 * empty day while the forest and the attempts beside it still showed yesterday, so the page named
 * two different days at once; the periods now sit on `sessions`, beside the daily summaries they
 * are. Every number here comes off the trade query's own totals (`view: history`), so the tiles and
 * the history table cannot disagree. Tones are signs, never verdicts: no threshold is applied to
 * fee drag.
 */
export function MeicSession({ mode, scope }: { mode: TradingMode; scope: MeicScope }) {
  const closed = useMeic(mode, { ...scope, view: "history", outcome: "all", reason: null, search: "", limit: 1, offset: 0 });
  const held = useMeic(mode, { ...scope, view: "positions", outcome: "all", reason: null, search: "", limit: 1, offset: 0 });
  const attempts = useAttempts("meic", mode, scope.day);

  const t = closed.data?.totals;
  const entered = held.data?.trades.total;
  const open = entered !== undefined && t !== undefined ? entered - t.trades : undefined;
  const costs = t !== undefined ? t.fees + t.settlementFees : undefined;
  const dragPct = t !== undefined && t.credit > 0 && costs !== undefined ? (costs / t.credit) * 100 : null;

  const armRows = attempts.data?.arms ?? [];
  const tries = armRows.reduce((n, r) => n + r.attempts, 0);
  const refusals = new Map<string, number>();
  for (const r of armRows) {
    for (const [reason, n] of Object.entries(r.refusals)) refusals.set(reason, (refusals.get(reason) ?? 0) + n);
  }
  const refusalRows = [...refusals.entries()].sort((x, y) => y[1] - x[1]);
  const refusedTotal = refusalRows.reduce((n, [, c]) => n + c, 0);

  return (
    <div className="grid-12">
      <StatTile
        label="net on this session"
        value={t !== undefined ? fmtMoney(t.net) : null}
        tone={t !== undefined && t.net >= 0 ? "pos" : "neg"}
        to="/meic/history"
        toLabel="the closed trades behind this net"
        foot={t === undefined ? "—" : `after fees and settlement · gross ${fmtMoney(t.gross)}`}
      />
      <StatTile
        label="positions"
        value={entered !== undefined ? String(entered) : null}
        to="/meic/positions"
        toLabel="every position this session held"
        foot={
          t === undefined || entered === undefined
            ? "—"
            : `${String(t.trades)} closed${open !== undefined && open > 0 ? ` · ${String(open)} still open` : ""}`
        }
      />
      <StatTile
        label="costs"
        value={costs !== undefined ? fmtMoney(costs) : null}
        tone="dim"
        to="/meic/exits"
        toLabel="fee drag across the era"
        title="trading fees plus settlement, over this session's closed trades"
        foot={
          t === undefined
            ? "—"
            : `fees ${fmtMoney(t.fees)} · settlement ${fmtMoney(t.settlementFees)}${dragPct !== null ? ` · ${dragPct.toFixed(1)}% of premium` : ""}`
        }
      />
      <StatTile
        label="evaluations"
        value={armRows.length === 0 ? null : tries.toLocaleString()}
        to="/meic/attempts"
        toLabel="every attempt on this session"
        foot={armRows.length === 0 ? "no attempts recorded on this session" : `over ${String(armRows.length)} arms · ${refusedTotal.toLocaleString()} refused`}
      />

      <GridCard
        label="net by arm"
        span={6}
        h={304}
        to="/meic/history"
        toLabel="the closed trades behind net by arm"
        foot={t === undefined ? (closed.isLoading ? "reading…" : "—") : `${String(t.trades)} closed trades · net of fees and settlement`}
      >
        <DivergingBars
          rows={(t?.byArm ?? []).map((r) => ({ label: r.arm, value: r.net, title: `${r.arm}: ${String(r.trades)} trades` }))}
          format={fmtMoney}
          emptyText={closed.isLoading ? "reading…" : "nothing closed on this session yet — 0DTE positions resolve at the close"}
        />
      </GridCard>

      <GridCard
        label="why entries were refused"
        span={6}
        h={304}
        to="/meic/attempts"
        toLabel="every refusal, arm by arm"
        foot={armRows.length === 0 ? "no attempts recorded on this session" : `${refusedTotal.toLocaleString()} refused evaluations · summed over ${String(armRows.length)} arms`}
      >
        <DivergingBars
          rows={refusalRows.map(([reason, n]) => ({ label: reason, value: n }))}
          format={(v) => v.toLocaleString()}
          tone="none"
          emptyText={attempts.isLoading ? "reading…" : "nothing refused on this session"}
        />
      </GridCard>
    </div>
  );
}
