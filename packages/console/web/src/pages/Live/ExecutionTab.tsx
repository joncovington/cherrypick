import type { ExecutionModule, ExecutionRecord, ExecutionWeek } from "@console/shared";
import { Card } from "../../components/DataTable";
import { fmtUsd } from "../../components/summary/kit";
import { useBridged } from "../../lib/bridged";

/**
 * The Live page's execution tab (B10, docs/trading-book-review-plan.md): each live arm's week beside
 * its P&L -- fill rate, slippage against the mid at submit, time to fill, and the human overrides
 * (a cleared kill block, a config change in session, a manual close). Below it, live against its
 * paper mirror over the sessions live was working, split into its parts with the largest named.
 * A losing week with clean execution reads differently from one with overrides. Read-only.
 */

const pct = (v: number | null) => (v === null ? "n/r" : `${Math.round(v * 100)}%`);
const usd = (v: number | null) => (v === null ? "n/r" : fmtUsd(v));
const tone = (v: number | null) => (v === null ? undefined : v >= 0 ? "num pnl-pos" : "num pnl-neg");

export function overrideLine(w: ExecutionWeek): string {
  if (w.clean) return "clean";
  const counts = new Map<string, number>();
  for (const o of w.overrides) counts.set(o.kind, (counts.get(o.kind) ?? 0) + 1);
  return [...counts.entries()].map(([k, n]) => `${k.replace(/_/g, " ")}${n > 1 ? ` ×${n}` : ""}`).join(", ");
}

export function gapLine(m: ExecutionModule): string | null {
  const g = m.gap;
  if (g === null || g.total === null) return null;
  const parts = Object.entries(g.parts)
    .map(([k, v]) => `${k} ${fmtUsd(v)}`)
    .join(" · ");
  return `live − ${m.mirror ?? "paper"} since ${g.since ?? "?"} over ${g.sessions} working session${g.sessions === 1 ? "" : "s"}: ${fmtUsd(g.total)} (${parts}); largest: ${g.largest ?? "none"}`;
}

export function windowLine(m: ExecutionModule): string | null {
  const w = m.window;
  if (w === null) return null;
  if (w.would === "too_few") return `window trigger (reported, never enforced): ${w.n} of ${w.window} live trades so far`;
  if (w.z === null || w.mean === null || w.baseline === null) return null;
  return `window trigger (reported, never enforced): last ${w.window} mean ${fmtUsd(w.mean)} vs baseline ${fmtUsd(w.baseline)}, z ${w.z.toFixed(2)} — would read ${w.would ?? "?"}`;
}

function ModuleCard({ m }: { m: ExecutionModule }) {
  if (!m.ok) {
    return (
      <Card title={m.module}>
        <p className="muted">{m.reason ?? "no live ledger"}</p>
      </Card>
    );
  }
  const gap = gapLine(m);
  const win = windowLine(m);
  return (
    <Card title={`${m.module} · paper mirror ${m.mirror ?? "?"}`}>
      <div className="table-scroll">
        <table className="data-table num-from-2">
          <thead>
            <tr>
              <th>Week</th>
              <th className="num">Net</th>
              <th className="num">Trades</th>
              <th className="num">Orders</th>
              <th className="num" title="filled ÷ (filled + missed)">Fill rate</th>
              <th className="num" title="fill − mid at submit, whole position; + is better than the model">Slip vs mid</th>
              <th className="num" title="mean, as a share of the structure's width">Slip % width</th>
              <th className="num">Min to fill</th>
              <th>Overrides</th>
            </tr>
          </thead>
          <tbody>
            {[...m.weeks].reverse().map((w) => (
              <tr key={w.week}>
                <td>{w.week}</td>
                <td className={tone(w.net)}>{fmtUsd(w.net)}</td>
                <td className="num">{w.trades}</td>
                <td className="num">{w.orders}</td>
                <td className="num">{pct(w.fillRate)}</td>
                <td className={tone(w.slipDollars)}>{usd(w.slipDollars)}</td>
                <td className="num">{w.slipPctWidthMean === null ? "n/r" : `${w.slipPctWidthMean.toFixed(1)}%`}</td>
                <td className="num">{w.minutesToFillMean === null ? "n/r" : w.minutesToFillMean.toFixed(1)}</td>
                <td className={w.clean ? "muted" : "pnl-neg"}>{overrideLine(w)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {gap ? <p className="muted">{gap}</p> : null}
      {win ? <p className={m.window?.would === "alarm" ? "pnl-neg" : "muted"}>{win}</p> : null}
    </Card>
  );
}

export function ExecutionTab() {
  const q = useBridged<ExecutionRecord>(["live-execution"], "/api/live/execution");
  if (q.isLoading) return <p className="muted">Reading the live ledgers…</p>;
  if (q.error || !q.data?.ok || !q.data.data) {
    return <p className="pnl-neg">{q.data?.error ?? String(q.error ?? "execution record unavailable")}</p>;
  }
  return (
    <>
      <p className="muted">
        Each live arm by week: fills against the mid at submit, and every human override flagged beside the week's P&L.
        Desk orders keep no ledger and are not read here.
      </p>
      {q.data.data.modules.map((m) => (
        <ModuleCard key={m.module} m={m} />
      ))}
    </>
  );
}
