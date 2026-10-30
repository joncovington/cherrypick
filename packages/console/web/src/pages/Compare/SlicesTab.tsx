import { useState } from "react";
import type { SliceCell, SliceDimensionRead, SliceReport, SliceReports } from "@console/shared";
import { Card } from "../../components/DataTable";
import { useBridged } from "../../lib/bridged";

/**
 * The Compare page's slices tab (B5, docs/trading-book-review-plan.md): an arm's in-sample slice
 * report from `python -m cherrypick.technicals slices`. Each cell shows n, its mean and a 95%
 * interval; a cell under 30 trades is greyed and never ranked. The header says how many cells there
 * are, so one strong cell reads in context. A good cell is a hypothesis to declare and test on held-out
 * data -- never a gate -- and the page says so first.
 *
 * Moved from Backtest 2026-10-30: the reports read paper and live ledgers, so a reader looks for them
 * beside the arms. A report may still come from a licensed replay, so the tab fails closed on the
 * backtest feature (`FAIL_CLOSED_SLIDES`) and the read stays `/api/backtest/slices`, gated on the server.
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

/** What each dimension measures, in words a reader without the code can follow. */
const DIMENSION_WORDS: Record<string, string> = {
  environment: "The market's trend when the trade was entered (uptrend, sideways, downtrend or mixed)",
  spx_vs_200d: "Whether the S&P 500 was above or below its 200-day average",
  atr_pct: "How much the S&P 500 was moving day to day (its average true range, as a share of price)",
  entry_hour: "The hour of the day the trade was entered (Eastern time)",
  holding: "How long the trade was held",
  setup: "The chart setup behind the entry",
};

export function dimensionLabel(name: string): string {
  return DIMENSION_WORDS[name] ?? name.replace(/_/g, " ");
}

/** "an average of 0.118R a trade" / "an average of $12.50 a trade". */
function perTrade(c: SliceCell | null, unit: string): string {
  return c === null ? "—" : `${fmtMean(c, unit)} a trade`;
}

export function overallSentence(r: SliceReport): string {
  const o = r.overall;
  const head = `Across all ${r.trades.toLocaleString()} trades the average was ${perTrade(o, r.unit)}`;
  const at = (v: number) => fmtMean({ ...o, mean: v }, r.unit);
  const range = o.ci95 === null ? "." : `, and the true average very likely sits between ${at(o.ci95[0])} and ${at(o.ci95[1])}.`;
  const verdict =
    r.analysis.overall === "positive"
      ? " That whole range is above zero, so on these trades the arm made money."
      : r.analysis.overall === "negative"
        ? " That whole range is below zero, so on these trades the arm lost money."
        : " That range crosses zero, so these trades cannot be told apart from breaking even.";
  return head + range + verdict;
}

export function dimensionSentence(d: SliceDimensionRead, unit: string): string {
  if (d.read === "one-group") {
    return `Every trade fell in one group${d.best ? ` (${d.best.value})` : ""}, so there is nothing to compare here.`;
  }
  if (d.read === "too-few") {
    return "Only one group has the 30 trades needed for a comparison, so this says nothing yet.";
  }
  const best = d.best!;
  const worst = d.worst!;
  if (d.read === "separated") {
    return (
      `This one matters. ${best.value} averaged ${perTrade(best, unit)} against ${perTrade(worst, unit)} for ${worst.value}, ` +
      "and their likely ranges do not overlap, so the gap is unlikely to be chance in these trades."
    );
  }
  return (
    `No clear difference. ${best.value} did best (${perTrade(best, unit)}) and ${worst.value} worst (${perTrade(worst, unit)}), ` +
    "but their likely ranges overlap, so the gap could be chance."
  );
}

/** The plain-English card above the tables (2026-10-28): what the report is, what it says, and what
 *  to do with it. Every judgement in it is the reader's (`analysis`); this only puts it in words. */
export function PlainEnglish({ r }: { r: SliceReport }) {
  const a = r.analysis;
  const who = a.arm ? `the ${a.arm} arm${a.schema ? ` (${a.schema})` : ""}` : r.label;
  const when = a.month ? ` in ${a.month}` : "";
  const separated = a.dimensions.filter((d) => d.read === "separated");
  const unitWords =
    r.unit === "R"
      ? "R is profit as a multiple of the risk taken: 0.10R means each trade made, on average, a tenth of what it put at risk."
      : "The unit is net dollars a trade, after fees.";
  return (
    <Card title="In plain English">
      <p>
        <strong>What this is.</strong> The {r.trades.toLocaleString()} closed trades of {who}
        {when}, sorted into groups by the conditions when each was entered. For each group you see how many trades it holds
        (n), the average result a trade (mean), and a range the true average very likely sits in (the 95% interval). {unitWords}
      </p>
      <p>
        <strong>Overall.</strong> {overallSentence(r)}
      </p>
      <ul>
        {a.dimensions.map((d) => (
          <li key={d.name}>
            <strong>{dimensionLabel(d.name)}.</strong> {dimensionSentence(d, r.unit)}
          </li>
        ))}
      </ul>
      <p>
        <strong>The upshot.</strong>{" "}
        {separated.length === 0
          ? "No condition separates the trades, so nothing here suggests a rule worth testing."
          : `${separated.length === 1 ? "One condition separates" : `${separated.length} conditions separate`} the trades: ${separated
              .map((d) => dimensionLabel(d.name).toLowerCase())
              .join("; ")}. That is a lead, not a rule.`}
      </p>
      <p className="muted">
        <strong>How to use it.</strong> This only describes trades that already happened, and the more groups you look at the
        more likely one looks good by luck. A difference here gets written down as a hypothesis (docs/hypotheses.json) and
        tested on data this report never saw before anything changes. The ranges are also a little narrower than they should
        be, because trades on the same day move together.
        {a.greyedCells > 0 ? ` ${a.greyedCells} group${a.greyedCells === 1 ? " has" : "s have"} under 30 trades and ${a.greyedCells === 1 ? "is" : "are"} shown faded, left out of every comparison.` : ""}
      </p>
    </Card>
  );
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

export function SlicesTab() {
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
      <PlainEnglish r={current} />
      <Report r={current} />
    </>
  );
}
