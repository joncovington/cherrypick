import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { TtWatchlistRow } from "@console/shared";
import { useWatchlist, useTtWatchlists, mutateJson } from "../../lib/api";
import { LiveQuoteRow } from "../../components/LiveQuote";
import { SkeletonRows, sortRows, useSort } from "../../components/DataTable";
import { TtWatchlistTable, EodCells, WatchlistHeadRow, WATCHLIST_SORT } from "../../components/TtWatchlistTable";
import { CollectorBanner } from "../../components/CollectorBanner";
import { TabStrip } from "../../components/ScopeBar";

export function WatchlistPage() {
  const [search, setSearch] = useSearchParams();
  const tab = search.get("tab") ?? "local";
  const tt = useTtWatchlists();
  const qc = useQueryClient();

  const setTab = (key: string) => {
    setSearch(key === "local" ? {} : { tab: key }, { replace: true });
  };

  const pin = useMutation({
    mutationFn: (v: { name: string; pinned: boolean }) => mutateJson("/api/tt-watchlists/pins", "POST", v),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["tt-watchlists"] }),
  });
  const [pickerOpen, setPickerOpen] = useState(false);

  const tabs = tt.data?.tabs ?? [];
  const activeIsKnown = tab === "local" || tabs.some((t) => t.key === tab);

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Watchlist</h1>
        {tt.data?.credential === false && (
          <span className="chip stale-note" title="no broker credential — showing cached lists only">
            no credential
          </span>
        )}
        {typeof tt.data?.lastError === "string" && (
          <span className="chip stale-note" title={tt.data.lastError}>
            broker fetch failed — cached
          </span>
        )}
      </div>

      <CollectorBanner />

      <div className="page-title-row">
        <TabStrip
          tabs={["local", ...tabs.map((t) => t.key)]}
          value={activeIsKnown ? tab : "local"}
          onChange={setTab}
          ariaLabel="watchlists"
          labels={{
            local: "Local",
            ...Object.fromEntries(
              tabs.map((t) => [
                t.key,
                <>
                  {t.name} <span className="muted">({t.count})</span>
                </>,
              ]),
            ),
          }}
        />
        <button type="button" className="btn btn-quiet" onClick={() => setPickerOpen((v) => !v)}>
          public lists…
        </button>
      </div>

      {pickerOpen && tt.data !== undefined && (
        <div className="card" style={{ padding: "0.5rem 1rem" }}>
          {tt.data.available.map((name) => (
            <label key={name} className="muted lbl" style={{ marginRight: "1rem" }}>
              <input
                type="checkbox"
                checked={tt.data!.pins.includes(name)}
                disabled={pin.isPending}
                onChange={(e) => pin.mutate({ name, pinned: e.target.checked })}
              />{" "}
              {name}
            </label>
          ))}
        </div>
      )}

      {tab === "local" || !activeIsKnown ? (
        <LocalWatchlistCard />
      ) : (
        <div className="cards cards-wide">
          <TtWatchlistTable tabKey={tab} />
        </div>
      )}
    </div>
  );
}

function LocalWatchlistCard() {
  const { data, isLoading } = useWatchlist();
  const sort = useSort();
  const rows = sortRows(data?.rows ?? [], sort, WATCHLIST_SORT);
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
    <div className="cards">
      <section className="card">
        <div className="page-title-row">
          <button
            type="button"
            className="btn"
            onClick={() => importScout.mutate()}
            disabled={importScout.isPending}
          >
            {importScout.isPending ? "importing…" : "import from scout"}
          </button>
        </div>
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
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <WatchlistHeadRow sort={sort} srcCol="" extra={2} />
              </thead>
              <tbody>
                {isLoading ? (
                  <SkeletonRows n={6} cols={14} />
                ) : data?.symbols.length === 0 ? (
                  <tr>
                    <td colSpan={14} className="muted">
                      empty — add a symbol or import from scout
                    </td>
                  </tr>
                ) : (
                  rows.map((r) => (
                    <WatchRow key={r.symbol} row={r} onRemove={() => remove.mutate(r.symbol)} />
                  ))
                )}
              </tbody>
            </table>
          </div>
      </section>
    </div>
  );
}

function WatchRow({ row, onRemove }: { row: TtWatchlistRow; onRemove: () => void }) {
  return (
    <LiveQuoteRow
      symbol={row.symbol}
      symbolTo={`/scout/builder?symbol=${encodeURIComponent(row.symbol)}`}
      trailing={
        <>
          <EodCells r={row} />
          <td>
            <button
              type="button"
              className="btn btn-quiet"
              onClick={onRemove}
              aria-label={`remove ${row.symbol}`}
            >
              ✕
            </button>
          </td>
        </>
      }
    />
  );
}
