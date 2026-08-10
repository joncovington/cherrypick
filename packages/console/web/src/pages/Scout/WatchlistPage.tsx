import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useWatchlist, mutateJson } from "../../lib/api";
import { LiveQuoteRow } from "../../components/LiveQuote";
import { SkeletonRows } from "../../components/DataTable";

export function WatchlistPage() {
  const { data, isLoading } = useWatchlist();
  const [input, setInput] = useState("");
  const qc = useQueryClient();
  const invalidate = () => void qc.invalidateQueries({ queryKey: ["watchlist"] });

  const add = useMutation({
    mutationFn: (symbol: string) => mutateJson("/api/watchlist", "POST", { symbol }),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (symbol: string) => mutateJson(`/api/watchlist/${symbol}`, "DELETE"),
    onSuccess: invalidate,
  });
  const importScout = useMutation({
    mutationFn: () => mutateJson<{ imported: number }>("/api/watchlist/import", "POST", {}),
    onSuccess: invalidate,
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const symbol = input.trim().toUpperCase();
    if (symbol !== "") {
      add.mutate(symbol);
      setInput("");
    }
  };

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Watchlist</h1>
        <button
          type="button"
          className="btn"
          onClick={() => importScout.mutate()}
          disabled={importScout.isPending}
        >
          {importScout.isPending ? "importing…" : "import from scout"}
        </button>
      </div>

      <div className="cards">
        <section className="card">
          <form onSubmit={submit} className="add-row">
            <input
              className="text-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="add symbol…"
              aria-label="add symbol"
            />
            <button type="submit" className="btn" disabled={add.isPending}>
              add
            </button>
          </form>
          <table className="data-table">
            <thead>
              <tr>
                <th>sym</th>
                <th>last</th>
                <th>bid</th>
                <th>ask</th>
                <th></th>
                <th></th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <SkeletonRows n={6} cols={7} />
              ) : data?.symbols.length === 0 ? (
                <tr>
                  <td colSpan={7} className="muted">
                    empty — add a symbol or import from scout
                  </td>
                </tr>
              ) : (
                data?.symbols.map((s) => (
                  <WatchRow key={s} symbol={s} onRemove={() => remove.mutate(s)} />
                ))
              )}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}

function WatchRow({ symbol, onRemove }: { symbol: string; onRemove: () => void }) {
  return (
    <LiveQuoteRow
      symbol={symbol}
      trailing={
        <>
          <td>
            <Link to={`/scout/symbol/${symbol}`} className="link">
              chart
            </Link>
          </td>
          <td>
            <button type="button" className="btn btn-quiet" onClick={onRemove} aria-label={`remove ${symbol}`}>
              ✕
            </button>
          </td>
        </>
      }
    />
  );
}
