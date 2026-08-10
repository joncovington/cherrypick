import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { mutateJson, useBlacklist, useChainEodStatus, useTtWatchlists } from "../../lib/api";
import { fmtMoney, SortTh, sortRows, useSort } from "../../components/DataTable";

interface CandidateLeg {
  kind: string;
  quantity: number;
  price: number;
  strike: number | null;
}

interface ScreenerRow {
  symbol: string;
  spot: number;
  ivRank: number | null;
  liquidity: number | null;
  expectedMove: number;
  directionalEdge: number | null;
  candidate: {
    strategy: string;
    legs: CandidateLeg[];
    credit: number;
    maxRisk: number | null;
    breakevens: number[];
    dte: number;
    expiration: string;
    pop: number | null;
    returnOnRisk: number | null;
    score: number | null;
  };
}

interface ScreenerResult {
  rows?: ScreenerRow[];
  skipped?: Array<{ symbol: string; reason: string }>;
  ranAt?: string;
  source?: string;
  quoteSource?: string;
  eodTradeDate?: string;
  error?: string;
}

interface ChainSnapshotResult {
  tradeDate?: string;
  captured?: number;
  skippedFresh?: number;
  skipped?: Array<{ symbol: string; reason: string }>;
  tookMs?: number;
  error?: string;
}

const SCREENER_SORT: Record<string, (r: ScreenerRow) => number | string | null> = {
  score: (r) => r.candidate.score,
  sym: (r) => r.symbol,
  strategy: (r) => r.candidate.strategy,
  exp: (r) => r.candidate.dte,
  credit: (r) => r.candidate.credit,
  risk: (r) => r.candidate.maxRisk,
  ror: (r) => r.candidate.returnOnRisk,
  pop: (r) => r.candidate.pop,
  ivr: (r) => r.ivRank,
  edge: (r) => r.directionalEdge,
};

function legsLabel(legs: CandidateLeg[]): string {
  return legs
    .map((l) => `${l.quantity > 0 ? "+" : ""}${l.quantity} ${l.strike ?? "stk"}${l.kind === "call" ? "C" : l.kind === "put" ? "P" : ""}`)
    .join(" / ");
}

export function ScreenerPage() {
  const [search] = useSearchParams();
  const [dteMin, setDteMin] = useState("25");
  const [dteMax, setDteMax] = useState("45");
  const [minIvRank, setMinIvRank] = useState("0");
  const [minLiquidity, setMinLiquidity] = useState("0");
  const [maxSymbols, setMaxSymbols] = useState("60");
  const [source, setSource] = useState(search.get("source") ?? "local");
  const [quoteSource, setQuoteSource] = useState<"live" | "eod">("eod");
  const tt = useTtWatchlists();
  const eod = useChainEodStatus();
  const qc = useQueryClient();

  const snapshot = useMutation({
    mutationFn: () => mutateJson<ChainSnapshotResult>("/api/chain-eod/run", "POST", { source: "all" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["chain-eod-status"] }),
  });

  const run = useMutation({
    mutationFn: () =>
      mutateJson<ScreenerResult>("/api/screener/run", "POST", {
        dteMin: Number(dteMin),
        dteMax: Number(dteMax),
        minIvRank: Number(minIvRank),
        minLiquidity: Number(minLiquidity),
        maxSymbols: Number(maxSymbols),
        source,
        quoteSource,
      }),
  });
  const result = run.data;
  const sort = useSort();
  const sortedRows = sortRows(result?.rows ?? [], sort, SCREENER_SORT);

  const sourceLabel =
    source === "local" ? "the local watchlist" : (tt.data?.tabs.find((t) => t.key === source)?.name ?? source);

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Screener</h1>
        <label className="muted lbl">
          list{" "}
          <select className="text-input" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="local">Local</option>
            {(tt.data?.tabs ?? []).map((t) => (
              <option key={t.key} value={t.key}>
                {t.name} ({t.count})
              </option>
            ))}
          </select>
        </label>
        <label className="muted lbl">
          quotes{" "}
          <select
            className="text-input"
            value={quoteSource}
            onChange={(e) => setQuoteSource(e.target.value === "eod" ? "eod" : "live")}
          >
            <option value="live">live</option>
            <option value="eod">
              EOD chain{eod.data?.latest ? ` (${eod.data.latest.tradeDate})` : " (none)"}
            </option>
          </select>
        </label>
        <label className="muted lbl">
          max syms{" "}
          <input
            className="text-input num-input"
            style={{ width: "3.5rem" }}
            value={maxSymbols}
            onChange={(e) => setMaxSymbols(e.target.value)}
          />
        </label>
        <label className="muted lbl">
          DTE <input className="text-input num-input" style={{ width: "3.5rem" }} value={dteMin} onChange={(e) => setDteMin(e.target.value)} />
          –<input className="text-input num-input" style={{ width: "3.5rem" }} value={dteMax} onChange={(e) => setDteMax(e.target.value)} />
        </label>
        <label className="muted lbl">
          min IV rank <input className="text-input num-input" style={{ width: "3.5rem" }} value={minIvRank} onChange={(e) => setMinIvRank(e.target.value)} />
        </label>
        <label className="muted lbl">
          min liq <input className="text-input num-input" style={{ width: "3rem" }} value={minLiquidity} onChange={(e) => setMinLiquidity(e.target.value)} />
        </label>
        <button type="button" className="btn" disabled={run.isPending} onClick={() => run.mutate()}>
          {run.isPending ? "screening…" : "Run screener"}
        </button>
        {result?.ranAt && <span className="chip">ran {new Date(result.ranAt).toLocaleTimeString()}</span>}
        {result?.eodTradeDate !== undefined && <span className="chip">EOD chain {result.eodTradeDate}</span>}
        <button
          type="button"
          className="btn btn-quiet"
          disabled={snapshot.isPending || eod.data?.running === true}
          onClick={() => snapshot.mutate()}
          title="capture today's EOD chain snapshot now (also runs automatically ~15:30 ET)"
        >
          {snapshot.isPending || eod.data?.running === true ? "snapshotting…" : "snapshot chains now"}
        </button>
        {snapshot.data !== undefined && (
          <span className="muted">
            {snapshot.data.error !== undefined
              ? snapshot.data.error
              : `captured ${snapshot.data.captured}, fresh ${snapshot.data.skippedFresh}, skipped ${snapshot.data.skipped?.length ?? 0}`}
          </span>
        )}
      </div>

      <div className="cards cards-wide">
        <section className="card">
          <h2>Candidates (over {sourceLabel})</h2>
          {run.isPending ? (
            <p className="muted">running — one batched metrics call, then chains + quote snapshots per survivor…</p>
          ) : result?.error ? (
            <p className="stale-note">{result.error}</p>
          ) : !result?.rows ? (
            <p className="muted">press Run screener — broker calls happen only on the button</p>
          ) : result.rows.length === 0 ? (
            <p className="muted">no candidates survived</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <SortTh label="score" k="score" sort={sort} />
                    <SortTh label="sym" k="sym" sort={sort} />
                    <SortTh label="strategy" k="strategy" sort={sort} />
                    <th>legs</th>
                    <SortTh label="exp (dte)" k="exp" sort={sort} />
                    <SortTh label="credit" k="credit" sort={sort} />
                    <SortTh label="max risk" k="risk" sort={sort} />
                    <SortTh label="RoR" k="ror" sort={sort} />
                    <SortTh label="POP" k="pop" sort={sort} />
                    <SortTh label="IVR" k="ivr" sort={sort} />
                    <SortTh label="edge" k="edge" sort={sort} />
                  </tr>
                </thead>
                <tbody>
                  {sortedRows.map((r, i) => (
                    <tr key={i}>
                      <td>{r.candidate.score !== null ? r.candidate.score.toFixed(3) : "—"}</td>
                      <td>{r.symbol}</td>
                      <td>{r.candidate.strategy.replace(/_/g, " ")}</td>
                      <td className="muted">{legsLabel(r.candidate.legs)}</td>
                      <td className="muted">
                        {r.candidate.expiration} ({r.candidate.dte})
                      </td>
                      <td>{fmtMoney(r.candidate.credit)}</td>
                      <td>{r.candidate.maxRisk !== null ? fmtMoney(-r.candidate.maxRisk) : "unbounded"}</td>
                      <td>{r.candidate.returnOnRisk !== null ? `${(r.candidate.returnOnRisk * 100).toFixed(1)}%` : "—"}</td>
                      <td>{r.candidate.pop !== null ? `${(r.candidate.pop * 100).toFixed(0)}%` : "—"}</td>
                      <td>{r.ivRank !== null ? r.ivRank.toFixed(0) : "—"}</td>
                      <td className={r.directionalEdge !== null && r.directionalEdge > 0 ? "pnl-pos" : "pnl-neg"}>
                        {r.directionalEdge !== null ? r.directionalEdge.toFixed(2) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {result?.skipped && result.skipped.length > 0 && (
            <p className="muted" style={{ marginBottom: 0 }}>
              skipped: {result.skipped.map((s) => `${s.symbol} (${s.reason})`).join(" · ")}
            </p>
          )}
        </section>
        <BlacklistCard />
      </div>
    </div>
  );
}

/** Learned + manual symbol blacklist (e.g. "no weekly options"). */
function BlacklistCard() {
  const { data } = useBlacklist();
  const qc = useQueryClient();
  const invalidate = () => void qc.invalidateQueries({ queryKey: ["blacklist"] });
  const [input, setInput] = useState("");
  const add = useMutation({
    mutationFn: (symbol: string) => mutateJson("/api/blacklist", "POST", { symbol }),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (symbol: string) => mutateJson(`/api/blacklist/${symbol}`, "DELETE"),
    onSuccess: invalidate,
  });
  const rows = data?.rows ?? [];
  return (
    <section className="card">
      <h2>Blacklist</h2>
      <p className="muted">
        skipped on every run before any broker call — auto-added when a chain shows no weekly options
      </p>
      <form
        className="add-row"
        onSubmit={(e) => {
          e.preventDefault();
          const symbol = input.trim().toUpperCase();
          if (symbol !== "") {
            add.mutate(symbol);
            setInput("");
          }
        }}
      >
        <input
          className="text-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="blacklist symbol…"
          aria-label="blacklist symbol"
        />
        <button type="submit" className="btn" disabled={add.isPending}>
          add
        </button>
      </form>
      {rows.length === 0 ? (
        <p className="muted">empty</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>sym</th>
              <th>reason</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol}>
                <td>{r.symbol}</td>
                <td className="muted">{r.reason}</td>
                <td>
                  <button
                    type="button"
                    className="btn btn-quiet"
                    onClick={() => remove.mutate(r.symbol)}
                    aria-label={`unblacklist ${r.symbol}`}
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
