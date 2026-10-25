import { useState } from "react";
import type { SliceCell, SliceReport, SliceReports } from "@console/shared";
import { Card } from "../../components/DataTable";
import { useBridged } from "../../lib/bridged";

/**
 * The Backtest page's slices tab (B5, docs/trading-book-review-plan.md): an arm's in-sample slice
 * report from `python -m cherrypick.technicals slices`. Each cell shows n, its mean and a 95%
 * interval; a cell under 30 trades is greyed and never ranked. The header says how many cells there
 * are, so one strong cell reads in context. A good cell is a hypothesis to declare and test on held-out
 * data -- never a gate -- and the page says so first.
 */

export function fmtMean(c: SliceCell, unit: string): string {
  if (c.mean === null) return "n/r";
  return unit === "R" ? `${c.mean.toFixed(3)}R` : `$${c.mean.toFixed(2)}`;
}

export function fmtCi(c: SliceCell, unit: string): string {
  if (c.ci95 === null) return "—";
  const f = (v: number) => (unit === "R" ? v.toFixed(3) : v.toFixed(2));
  return `${f(c.ci95[0])} to ${f(c.ci95[1])}`;
}

function cellTone(c: SliceCell): string | undefined {
  if (c.greyed || c.ci95 === null) return "num muted";
  if (c.ci95[0] > 0) return "num pnl-pos";
  if (c.ci95[1] < 0) return "num pnl-neg";
  return "num";
}

function Report({ r }: { r: SliceReport }) {
  return (
    <Card title={`${r.label} · ${r.trades} trades · ${r.cells} cells (${r.rankedCells} with 30+)`}>
      <p className="muted">
        In-sample. Overall {fmtMean(r.overall, r.unit)} ({fmtCi(r.overall, r.unit)}). {r.note}
      </p>
      {r.dimensions.map((d) => (
        <div className="table-scroll" key={d.name}>
          <table className="data-table num-from-2">
            <thead>
              <tr>
                <th>{d.name.replace(/_/g, " ")}</th>
                <th className="num">n</th>
                <th className="num">Mean</th>
                <th className="num">95% interval</th>
              </tr>
            </thead>
            <tbody>
              {d.cells.map((c) => (
                <tr key={c.value} className={c.greyed ? "muted" : undefined} title={c.greyed ? "under 30 trades: shown, not ranked" : undefined}>
                  <td>{c.value}</td>
                  <td className="num">{c.n}</td>
                  <td className={cellTone(c)}>{fmtMean(c, r.unit)}</td>
                  <td className="num">{fmtCi(c, r.unit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </Card>
  );
}

export function BacktestSlicesTab() {
  const q = useBridged<SliceReports>(["backtest-slices"], "/api/backtest/slices", 300_000);
  const [pick, setPick] = useState<string | null>(null);
  if (q.isLoading) return <p className="muted">Reading the slice reports…</p>;
  if (q.error || !q.data?.ok || !q.data.data) return <p className="pnl-neg">{q.data?.error ?? "slice reports unavailable"}</p>;
  const { reports, dir } = q.data.data;
  if (reports.length === 0) {
    return (
      <p className="muted">
        No slice reports yet. Write one with <code>python -m cherrypick.technicals slices --db &lt;ledger&gt; --schema &lt;schema&gt; --arm &lt;arm&gt;</code>{" "}
        (into {dir}).
      </p>
    );
  }
  const current = reports.find((r) => r.file === pick) ?? reports[reports.length - 1]!;
  return (
    <>
      <p className="muted">
        A description of the trades given, never a gate: a cell that looks good is declared in docs/hypotheses.json and tested
        on held-out data, corrected for the cells looked at.
      </p>
      <label>
        Report{" "}
        <select value={current.file} onChange={(e) => setPick(e.target.value)}>
          {reports.map((r) => (
            <option key={r.file} value={r.file}>
              {r.label}
            </option>
          ))}
        </select>
      </label>
      <Report r={current} />
    </>
  );
}
