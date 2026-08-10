import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { mutateJson } from "../../lib/api";
import { fmtMoney } from "../../components/DataTable";

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
  error?: string;
}

function legsLabel(legs: CandidateLeg[]): string {
  return legs
    .map((l) => `${l.quantity > 0 ? "+" : ""}${l.quantity} ${l.strike ?? "stk"}${l.kind === "call" ? "C" : l.kind === "put" ? "P" : ""}`)
    .join(" / ");
}

export function ScreenerPage() {
  const [dteMin, setDteMin] = useState("25");
  const [dteMax, setDteMax] = useState("45");
  const [minIvRank, setMinIvRank] = useState("0");
  const [minLiquidity, setMinLiquidity] = useState("0");

  const run = useMutation({
    mutationFn: () =>
      mutateJson<ScreenerResult>("/api/screener/run", "POST", {
        dteMin: Number(dteMin),
        dteMax: Number(dteMax),
        minIvRank: Number(minIvRank),
        minLiquidity: Number(minLiquidity),
      }),
  });
  const result = run.data;

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Screener</h1>
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
      </div>

      <div className="cards cards-wide">
        <section className="card">
          <h2>Candidates (over the watchlist)</h2>
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
                    <th>score</th>
                    <th>sym</th>
                    <th>strategy</th>
                    <th>legs</th>
                    <th>exp (dte)</th>
                    <th>credit</th>
                    <th>max risk</th>
                    <th>RoR</th>
                    <th>POP</th>
                    <th>IVR</th>
                    <th>edge</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((r, i) => (
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
      </div>
    </div>
  );
}
