import type { BacktestScorecard, BacktestScorecardStats } from "@console/shared";
import { fmtCash, fmtCount, fmtNum } from "../../lib/format";

/**
 * The stats table (arms as columns, so they read side by side) and one arm's insights. Run cards and
 * study reports both carry `scorecard.scorecard`'s shape; the console prints it and decides nothing.
 */

type Row = { label: string; title?: string; cell: (s: BacktestScorecardStats) => string; tone?: (s: BacktestScorecardStats) => string | undefined };

function signed(v: number | null): string | undefined {
  return v === null || v === 0 ? undefined : v > 0 ? "pnl-pos" : "pnl-neg";
}

const ROWS: Row[] = [
  { label: "Total P/L", cell: (s) => fmtCash(s.total_pl), tone: (s) => signed(s.total_pl) },
  {
    label: "Max drawdown",
    title: "dollars below the running peak of cumulative P/L",
    cell: (s) => fmtCash(s.max_drawdown === null || s.max_drawdown === 0 ? s.max_drawdown : -s.max_drawdown),
    tone: (s) => (s.max_drawdown ? "pnl-neg" : undefined),
  },
  {
    label: "Return on DD",
    title: "total P/L over max drawdown (not annualised)",
    cell: (s) => (s.return_on_dd === null ? "n/a" : `${(s.return_on_dd * 100).toFixed(1)}%`),
    tone: (s) => signed(s.return_on_dd),
  },
  { label: "High P/L", title: "highest cumulative P/L reached", cell: (s) => fmtCash(s.high_pl), tone: (s) => signed(s.high_pl) },
  { label: "Low P/L", title: "lowest cumulative P/L reached, from a start of zero", cell: (s) => fmtCash(s.low_pl), tone: (s) => signed(s.low_pl) },
  {
    label: "Profit factor",
    title: "gross wins over gross losses",
    cell: (s) => (s.profit_factor === null ? "n/a" : `${fmtNum(s.profit_factor)}${s.profit_factor_basis === "sessions" ? " (sessions)" : ""}`),
  },
  { label: "Sharpe", title: "annualised from sessions; for reading, not the verdict (the deflated Sharpe decides)", cell: (s) => fmtNum(s.sharpe) },
  { label: "Sortino", title: "annualised from sessions, over downside deviation", cell: (s) => fmtNum(s.sortino) },
  { label: "Trades", cell: (s) => fmtCount(s.trades) },
  { label: "Sessions traded", cell: (s) => `${fmtCount(s.traded_sessions)} of ${fmtCount(s.sessions)}` },
];

export function ScorecardTable({ cards, highlight }: { cards: Record<string, BacktestScorecard | undefined>; highlight?: string }) {
  const names = Object.keys(cards).filter((n) => cards[n] !== undefined);
  if (names.length === 0) return null;
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Stats</th>
            {names.map((n) => (
              <th key={n} className="num" style={n === highlight ? { textDecoration: "underline" } : undefined}>
                {n}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ROWS.map((r) => (
            <tr key={r.label}>
              <td title={r.title}>{r.label}</td>
              {names.map((n) => {
                const s = cards[n]!.stats;
                return (
                  <td key={n} className={`num ${r.tone?.(s) ?? ""}`}>
                    {r.cell(s)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Insights({ card }: { card: BacktestScorecard | undefined }) {
  if (card === undefined || card.insights.length === 0) return null;
  const ordered = [...card.insights].sort((a, b) => Number(b.flag) - Number(a.flag));
  return (
    <ul style={{ margin: "4px 0 8px", paddingLeft: 18 }}>
      {ordered.map((x) => (
        <li key={x.key} className={x.flag ? undefined : "muted"}>
          {x.flag && <span className="chip chip-warn" style={{ marginRight: 6 }}>look</span>}
          {x.text}
        </li>
      ))}
    </ul>
  );
}
