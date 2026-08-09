import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { SkeletonRows } from "../../components/DataTable";

export interface ChainSide {
  streamerSymbol: string;
  bid: number | null;
  ask: number | null;
  delta: number | null;
  iv: number | null;
  openInterest: number | null;
  quoteAge: number | null;
}

export interface ChainRow {
  strike: number;
  call: ChainSide | null;
  put: ChainSide | null;
}

interface ChainPayload {
  symbol: string;
  expiration: string | null;
  expirations: string[];
  rows: ChainRow[];
}

/** What the builder currently holds, so the chain can mark selected strikes. */
export interface LegMark {
  kind: "call" | "put";
  strike: number;
  quantity: number;
}

async function getChain(symbol: string, expiration: string | null): Promise<ChainPayload> {
  const url = `/api/chain/${symbol}${expiration !== null ? `?expiration=${expiration}` : ""}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`chain: HTTP ${res.status}`);
  return (await res.json()) as ChainPayload;
}

export function useChain(symbol: string, expiration: string | null) {
  return useQuery<ChainPayload>({
    queryKey: ["chain", symbol, expiration],
    queryFn: () => getChain(symbol, expiration),
    refetchInterval: 15_000,
  });
}

function fmt(v: number | null, digits = 2): string {
  return v === null ? "—" : v.toFixed(digits);
}

function fmtOi(v: number | null): string {
  if (v === null) return "—";
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

function mid(side: ChainSide): number | null {
  if (side.bid === null || side.ask === null) return null;
  return (side.bid + side.ask) / 2;
}

interface Props {
  symbol: string;
  expiration: string | null;
  onExpiration: (exp: string) => void;
  /** bid = sell (short), ask = buy (long); price is ALWAYS the mid. */
  onPick: (pick: {
    kind: "call" | "put";
    strike: number;
    quantity: 1 | -1;
    price: number | null;
    delta: number | null;
    expiration: string | null;
  }) => void;
  spot: number | null;
  legs: LegMark[];
}

/**
 * Chain with delta and open interest flanking bid/ask on both sides. Clicking
 * a bid sells the strike, an ask buys it — both priced at the mid. Strikes the
 * builder currently holds carry a red (short) or green (long) row border with
 * an STO/BTO badge; the expected-move band tints the strike column.
 */
export function ChainPanel({ symbol, expiration, onExpiration, onPick, spot, legs }: Props) {
  const [open, setOpen] = useState(true);
  const { data, isLoading } = useChain(symbol, expiration);

  const rows = data?.rows ?? [];
  const visible =
    spot !== null && rows.length > 30
      ? [...rows].sort((a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot)).slice(0, 24).sort((a, b) => a.strike - b.strike)
      : rows;

  // Expected move from the ATM call's IV: spot * iv * sqrt(dte/365).
  let expectedMove: number | null = null;
  if (spot !== null && data?.expiration != null) {
    const dte = Math.max(0, (Date.parse(data.expiration) - Date.now()) / 86_400_000);
    const atm = [...visible]
      .filter((r) => r.call?.delta != null && r.call.iv != null && r.call.iv > 0 && r.call.iv < 5)
      .sort((a, b) => Math.abs((a.call?.delta ?? 1) - 0.5) - Math.abs((b.call?.delta ?? 1) - 0.5))[0];
    if (atm?.call?.iv != null) expectedMove = spot * atm.call.iv * Math.sqrt(dte / 365);
  }

  const netAt = (kind: "call" | "put", strike: number): number =>
    legs.filter((l) => l.kind === kind && l.strike === strike).reduce((s, l) => s + l.quantity, 0);

  return (
    <section className="card">
      <div className="panel-head-row">
        <button
          type="button"
          className="btn btn-quiet collapse-toggle"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={open ? "collapse chain" : "expand chain"}
        >
          {open ? "▾" : "▸"}
        </button>
        <h2>Chain</h2>
        {data && data.expirations.length > 0 && (
          <select
            className="text-input"
            value={data.expiration ?? ""}
            onChange={(e) => onExpiration(e.target.value)}
            aria-label="expiration"
          >
            {data.expirations.map((e) => (
              <option key={e} value={e}>
                {e}
              </option>
            ))}
          </select>
        )}
        {expectedMove !== null && (
          <span className="chip chip-em" title="expected move from the ATM call's IV">
            ±EM {expectedMove.toFixed(2)}
          </span>
        )}
        <span className="muted lbl">bid sells · ask buys · priced at mid</span>
      </div>
      {open && (
      <div className="table-scroll">
        <table className="data-table chain-table">
          <thead>
            <tr>
              <th>OI</th>
              <th>Δ</th>
              <th>bid</th>
              <th>ask</th>
              <th className="chain-strike">strike</th>
              <th>bid</th>
              <th>ask</th>
              <th>Δ</th>
              <th>OI</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <SkeletonRows n={10} cols={10} />
            ) : visible.length === 0 ? (
              <tr>
                <td colSpan={10} className="muted">
                  no chain in the stream cache for {symbol} — the streamer caches a spot + ATM window
                  per requested underlying
                </td>
              </tr>
            ) : (
              visible.map((r) => {
                const net = netAt("call", r.strike) + netAt("put", r.strike);
                const held = net !== 0;
                const inBand =
                  expectedMove !== null && spot !== null && Math.abs(r.strike - spot) <= expectedMove;
                const rowCls = held ? (net > 0 ? "chain-held-long" : "chain-held-short") : "";
                return (
                  <tr key={r.strike} className={rowCls}>
                    <td className="muted">{r.call ? fmtOi(r.call.openInterest) : "—"}</td>
                    <td>{r.call ? fmt(r.call.delta) : "—"}</td>
                    <ChainCell
                      side={r.call}
                      onSell={() => r.call && onPick({ kind: "call", strike: r.strike, quantity: -1, price: mid(r.call), delta: r.call.delta, expiration: data?.expiration ?? null })}
                      onBuy={() => r.call && onPick({ kind: "call", strike: r.strike, quantity: 1, price: mid(r.call), delta: r.call.delta, expiration: data?.expiration ?? null })}
                    />
                    <td className={`chain-strike ${inBand ? "chain-em-band" : ""}`}>{r.strike}</td>
                    <ChainCell
                      side={r.put}
                      onSell={() => r.put && onPick({ kind: "put", strike: r.strike, quantity: -1, price: mid(r.put), delta: r.put.delta, expiration: data?.expiration ?? null })}
                      onBuy={() => r.put && onPick({ kind: "put", strike: r.strike, quantity: 1, price: mid(r.put), delta: r.put.delta, expiration: data?.expiration ?? null })}
                    />
                    <td>{r.put ? fmt(r.put.delta) : "—"}</td>
                    <td className="muted">{r.put ? fmtOi(r.put.openInterest) : "—"}</td>
                    <td className="chain-badge-cell">
                      {held && (
                        <span className={`chain-badge ${net > 0 ? "chain-badge-long" : "chain-badge-short"}`}>
                          {net > 0 ? "BTO" : "STO"} {Math.abs(net)}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      )}
    </section>
  );
}

/** bid sells (short), ask buys (long) — both at the mid. */
function ChainCell({
  side,
  onSell,
  onBuy,
}: {
  side: ChainSide | null;
  onSell: () => void;
  onBuy: () => void;
}) {
  if (side === null) {
    return (
      <>
        <td className="muted">—</td>
        <td className="muted">—</td>
      </>
    );
  }
  const stale = side.quoteAge !== null && side.quoteAge > 600;
  return (
    <>
      <td className={`chain-click chain-sell ${stale ? "muted" : ""}`} onClick={onSell} title="sell to open — short leg at the mid">
        {fmt(side.bid)}
      </td>
      <td className={`chain-click chain-buy ${stale ? "muted" : ""}`} onClick={onBuy} title="buy to open — long leg at the mid">
        {fmt(side.ask)}
      </td>
    </>
  );
}
