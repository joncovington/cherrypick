import { useState } from "react";
import type { BacktestTrade, BacktestTradeLog } from "@console/shared";
import { signTone, useBridged } from "../../lib/bridged";
import { fmtCash, fmtCount } from "../../lib/format";
import { TradePath } from "./TradePath";

/**
 * One arm's trade log (`read trades`, `cards.trade_log`, 2026-10-31): every trade the run kept, with
 * when it opened and closed, how it ended in the module's own words, and its money in the suite's
 * columns (gross, fees, settlement and slippage apart, net). Closed until asked for: a run's arm
 * can hold a thousand trades, and the report stays light without them. Sorting and the
 * winners/losers filter reorder the module's rows; nothing here computes a figure. A MEIC trade
 * opens its path through the session (`TradePath`).
 */

export type TradeSort = "closed" | "net-desc" | "net-asc";
export type TradeFilter = "all" | "winners" | "losers";

/** The rows to show: filtered by the sign of their net, then sorted. Pure. */
export function arrangeTrades(trades: BacktestTrade[], sort: TradeSort, filter: TradeFilter): BacktestTrade[] {
  const kept = trades.filter((t) =>
    filter === "all" ? true : filter === "winners" ? (t.net_pnl ?? 0) > 0 : (t.net_pnl ?? 0) < 0,
  );
  if (sort === "closed") return kept;
  // A trade with no recorded net sorts last either way: it is unknown, not zero.
  const sign = sort === "net-desc" ? -1 : 1;
  return [...kept].sort((a, b) => {
    if (a.net_pnl === null) return b.net_pnl === null ? 0 : 1;
    if (b.net_pnl === null) return -1;
    return sign * (a.net_pnl - b.net_pnl);
  });
}

/** "2024-01-02T13:05:00" -> "2024-01-02 13:05". */
function when(v: string | null): string {
  return v === null ? "—" : v.replace("T", " ").slice(0, 16);
}

/** `pathable`: the run's module has a trade path (MEIC), so a row with a position id can open one. */
export function TradeTable({ log, pathable = false }: { log: BacktestTradeLog; pathable?: boolean }) {
  const [sort, setSort] = useState<TradeSort>("closed");
  const [filter, setFilter] = useState<TradeFilter>("all");
  const [opened, setOpened] = useState<BacktestTrade | null>(null);
  const rows = arrangeTrades(log.trades, sort, filter);
  const show = (k: keyof BacktestTrade) => log.trades.some((t) => t[k] !== null);
  return (
    <>
      {opened !== null && opened.position_id !== null && (
        <div>
          <button type="button" className="btn btn-quiet" onClick={() => setOpened(null)}>
            ✕ close the path
          </button>
          <TradePath runId={log.run_id} arm={log.arm} session={opened.session} position={opened.position_id} />
        </div>
      )}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", margin: "6px 0", fontSize: 12 }}>
        <label className="muted">
          sort{" "}
          <select value={sort} onChange={(e) => setSort(e.target.value as TradeSort)}>
            <option value="closed">by close</option>
            <option value="net-desc">best first</option>
            <option value="net-asc">worst first</option>
          </select>
        </label>
        <label className="muted">
          show{" "}
          <select value={filter} onChange={(e) => setFilter(e.target.value as TradeFilter)}>
            <option value="all">all</option>
            <option value="winners">winners</option>
            <option value="losers">losers</option>
          </select>
        </label>
        <span className="muted">{`${fmtCount(rows.length)} of ${fmtCount(log.trades.length)} trades`}</span>
      </div>
      <div className="table-scroll" style={{ maxHeight: 420, overflowY: "auto" }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>opened</th>
              <th>closed</th>
              <th>how it ended</th>
              <th className="num">gross</th>
              <th className="num">fees</th>
              {show("settlement") && <th className="num">settlement</th>}
              {show("slippage") && <th className="num">slippage</th>}
              <th className="num">net</th>
              {show("capital") && <th className="num">at risk</th>}
              {pathable && <th aria-label="path" />}
            </tr>
          </thead>
          <tbody>
            {rows.map((t, i) => (
              <tr key={`${t.position_id ?? ""}|${t.exit_time ?? t.session}|${String(i)}`}>
                <td>{when(t.entry_time)}</td>
                <td>{when(t.exit_time ?? t.session)}</td>
                <td>{t.outcome ?? <span className="muted">not recorded</span>}</td>
                <td className="num">{fmtCash(t.gross_pnl)}</td>
                <td className="num">{fmtCash(t.fees)}</td>
                {show("settlement") && <td className="num">{fmtCash(t.settlement)}</td>}
                {show("slippage") && <td className="num">{fmtCash(t.slippage)}</td>}
                <td className={`num ${signTone(t.net_pnl)}`}>{fmtCash(t.net_pnl)}</td>
                {show("capital") && <td className="num">{fmtCash(t.capital)}</td>}
                {pathable && (
                  <td>
                    {t.position_id !== null && (
                      <button type="button" className="btn btn-quiet" aria-pressed={opened === t} onClick={() => setOpened(t)}>
                        path
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function LoadedLog({ runId, arm, pathable }: { runId: string; arm: string; pathable: boolean }) {
  const url = `/api/backtest/runs/${runId}/trades?arm=${encodeURIComponent(arm)}`;
  // A finished run's trades do not change: read once, no polling.
  const { data, isLoading, isError, error } = useBridged<BacktestTradeLog>(["backtest", "trades", runId, arm], url, 0);
  if (isLoading) return <p className="muted">Reading the trades…</p>;
  if (isError) return <p className="pnl-neg">Trade log unavailable: {String(error)}</p>;
  if (!data?.ok || data.data === null) return <p className="pnl-neg">Trade log unavailable: {data?.error ?? "no answer"}</p>;
  if (data.data.trades.length === 0) return <p className="muted">This arm kept no trades.</p>;
  return <TradeTable log={data.data} pathable={pathable} />;
}

export function TradeLog({ runId, arm, pathable = false }: { runId: string; arm: string; pathable?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" className="btn btn-quiet" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "▾ hide the trades" : "▸ every trade"}
      </button>
      {open && <LoadedLog key={`${runId}|${arm}`} runId={runId} arm={arm} pathable={pathable} />}
    </div>
  );
}
