import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { CandleWarmResult, TtWatchlistRow } from "@console/shared";
import { mutateJson, useTtWatchlist } from "../lib/api";
import { LiveQuoteRow } from "./LiveQuote";
import { SkeletonRows, SortTh, sortRows, useSort } from "./DataTable";

import { fmtNum as fmt, fmtPctSigned as pct } from "../lib/format";

/** tasty-style compact numbers: 13.3K, 4.2M, 225B. */
function compact(v: number | null): string {
  if (v === null) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e12) return `${(v / 1e12).toFixed(1)}T`;
  if (abs >= 1e9) return `${(v / 1e9).toFixed(0)}B`;
  if (abs >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toFixed(0);
}

/** Column accessors shared by every watchlist table (local card included). */
export const WATCHLIST_SORT: Record<string, (r: TtWatchlistRow) => number | string | null> = {
  sym: (r) => r.symbol,
  last: (r) => r.last ?? r.eodClose,
  bid: (r) => r.bid,
  ask: (r) => r.ask,
  eod: (r) => r.eodClose,
  chg: (r) => r.eodChangePct,
  ivr: (r) => r.ivRank,
  ivx: (r) => r.ivIndex,
  vol: (r) => r.volume,
  cap: (r) => r.marketCap,
  hi: (r) => r.yearHigh,
  lo: (r) => r.yearLow,
};

/** The shared sortable header row; `extra` = trailing unsortable cells. */
export function WatchlistHeadRow({
  sort,
  srcCol,
  extra,
}: {
  sort: ReturnType<typeof useSort>;
  srcCol: string;
  extra: number;
}) {
  return (
    <tr>
      <SortTh label="sym" k="sym" sort={sort} />
      <SortTh label="last" k="last" sort={sort} />
      <SortTh label="bid" k="bid" sort={sort} />
      <SortTh label="ask" k="ask" sort={sort} />
      <SortTh label={srcCol} k="eod" sort={sort} />
      <SortTh label="chg%" k="chg" sort={sort} />
      <SortTh label="IVR" k="ivr" sort={sort} />
      <SortTh label="IV%" k="ivx" sort={sort} />
      <SortTh label="vol" k="vol" sort={sort} />
      <SortTh label="mkt cap" k="cap" sort={sort} />
      <SortTh label="1y high" k="hi" sort={sort} />
      <SortTh label="1y low" k="lo" sort={sort} />
      {Array.from({ length: extra }, (_, i) => (
        <th key={i}></th>
      ))}
    </tr>
  );
}

/** One tastytrade watchlist as a read-only table: cached quotes + EOD context.
 *  Live WS rows only for small lists — a 200-symbol public list stays static. */
export function TtWatchlistTable({ tabKey }: { tabKey: string }) {
  const { data, isLoading } = useTtWatchlist(tabKey);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const sort = useSort();
  const rows = sortRows(data?.rows ?? [], sort, WATCHLIST_SORT);

  const refresh = useMutation({
    mutationFn: () => mutateJson("/api/tt-watchlists/refresh", "POST", {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tt-watchlists"] });
      void qc.invalidateQueries({ queryKey: ["tt-watchlist"] });
    },
  });
  const warm = useMutation({
    mutationFn: () =>
      mutateJson<CandleWarmResult | { error: string }>("/api/candles/warm", "POST", { source: tabKey }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["tt-watchlist", tabKey] }),
  });
  const warmResult = warm.data;

  return (
    <section className="card">
      <div className="page-title-row">
        <h2 style={{ margin: 0 }}>
          {data?.tab.name ?? tabKey}
          {data !== undefined && <span className="chip">{data.tab.count} symbols</span>}
          {data?.tab.stale === true && <span className="chip stale-note">stale</span>}
        </h2>
        <button type="button" className="btn" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
          {refresh.isPending ? "refreshing…" : "refresh list"}
        </button>
        <button type="button" className="btn" disabled={warm.isPending} onClick={() => warm.mutate()}>
          {warm.isPending ? "warming EOD…" : "warm EOD data"}
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => navigate(`/scout/screener?source=${encodeURIComponent(tabKey)}`)}
        >
          screen this list
        </button>
      </div>
      {warmResult !== undefined && (
        <p className="muted">
          {"error" in warmResult
            ? warmResult.error
            : `warmed ${warmResult.warmed}, fresh ${warmResult.skippedFresh}` +
              (warmResult.failed.length > 0 ? `, failed ${warmResult.failed.length}` : "") +
              ` (${Math.round(warmResult.tookMs / 1000)}s)`}
        </p>
      )}
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <WatchlistHeadRow sort={sort} srcCol={data?.live === true ? "" : "eod"} extra={1} />
          </thead>
          <tbody>
            {isLoading || data === undefined ? (
              <SkeletonRows n={8} cols={13} />
            ) : data.rows.length === 0 ? (
              <tr>
                <td colSpan={13} className="muted">
                  empty list
                </td>
              </tr>
            ) : data.live ? (
              rows.map((r) => (
                <LiveQuoteRow
                  key={r.symbol}
                  symbol={r.symbol}
                  symbolTo={`/scout/builder?symbol=${encodeURIComponent(r.symbol)}`}
                  trailing={<EodCells r={r} />}
                />
              ))
            ) : (
              rows.map((r) => <StaticRow key={r.symbol} r={r} />)
            )}
          </tbody>
        </table>
      </div>
      {data !== undefined && data.skipped.length > 0 && (
        <p className="muted" style={{ marginBottom: 0 }}>
          not shown (unsupported symbols): {data.skipped.join(", ")}
        </p>
      )}
    </section>
  );
}

export function EodCells({ r }: { r: TtWatchlistRow }) {
  return (
    <>
      <td className={r.eodChangePct !== null && r.eodChangePct < 0 ? "pnl-neg" : "pnl-pos"}>
        {pct(r.eodChangePct)}
      </td>
      <td>{r.ivRank !== null ? r.ivRank.toFixed(1) : "—"}</td>
      <td>{r.ivIndex !== null ? `${r.ivIndex.toFixed(1)}%` : "—"}</td>
      <td className="muted">{compact(r.volume)}</td>
      <td className="muted">{compact(r.marketCap)}</td>
      <td className="muted">{fmt(r.yearHigh)}</td>
      <td className="muted">{fmt(r.yearLow)}</td>
      <td>
        <Link to={`/scout/symbol/${r.symbol}`} className="link">
          chart
        </Link>
        {!r.candleFresh && (
          <span className="muted" title="EOD candles missing or stale — warm EOD data">
            {" "}
            ○
          </span>
        )}
      </td>
    </>
  );
}

function StaticRow({ r }: { r: TtWatchlistRow }) {
  return (
    <tr>
      <td>
        <Link to={`/scout/builder?symbol=${encodeURIComponent(r.symbol)}`} className="link">
          {r.symbol}
        </Link>
      </td>
      <td>{fmt(r.last)}</td>
      <td className="muted">{fmt(r.bid)}</td>
      <td className="muted">{fmt(r.ask)}</td>
      <td className="muted">{fmt(r.eodClose)}</td>
      <EodCells r={r} />
    </tr>
  );
}
