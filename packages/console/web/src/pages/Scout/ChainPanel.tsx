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
  /** Called when the user picks a side at a strike; price is the mid when known, delta rides along. */
  onPick: (pick: { kind: "call" | "put"; strike: number; price: number | null; delta: number | null }) => void;
  spot: number | null;
}

/**
 * Chain with the reads that matter when selecting strikes: delta and open
 * interest flank the bid/ask on both sides. Calls left, strike center, puts
 * right; click a side to send it to the leg list.
 */
export function ChainPanel({ symbol, expiration, onExpiration, onPick, spot }: Props) {
  const { data, isLoading } = useChain(symbol, expiration);

  const rows = data?.rows ?? [];
  // Focus the view around the money when we know spot: nearest 24 strikes.
  const visible =
    spot !== null && rows.length > 30
      ? [...rows].sort((a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot)).slice(0, 24).sort((a, b) => a.strike - b.strike)
      : rows;

  return (
    <section className="card">
      <div className="panel-head-row">
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
        <span className="muted lbl">click a side to add it as a leg</span>
      </div>
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
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <SkeletonRows n={10} cols={9} />
            ) : visible.length === 0 ? (
              <tr>
                <td colSpan={9} className="muted">
                  no chain in the stream cache for {symbol} — the streamer caches a spot + ATM window
                  per requested underlying
                </td>
              </tr>
            ) : (
              visible.map((r) => {
                const atm = spot !== null && Math.abs(r.strike - spot) === Math.min(...visible.map((v) => Math.abs(v.strike - spot)));
                return (
                  <tr key={r.strike} className={atm ? "chain-atm" : ""}>
                    <td className="muted">{r.call ? fmtOi(r.call.openInterest) : "—"}</td>
                    <td>{r.call ? fmt(r.call.delta) : "—"}</td>
                    <ChainCell side={r.call} onClick={() => r.call && onPick({ kind: "call", strike: r.strike, price: mid(r.call), delta: r.call.delta })} />
                    <td className="chain-strike">{r.strike}</td>
                    <ChainCell side={r.put} onClick={() => r.put && onPick({ kind: "put", strike: r.strike, price: mid(r.put), delta: r.put.delta })} />
                    <td>{r.put ? fmt(r.put.delta) : "—"}</td>
                    <td className="muted">{r.put ? fmtOi(r.put.openInterest) : "—"}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** bid + ask as one clickable pair of cells. */
function ChainCell({ side, onClick }: { side: ChainSide | null; onClick: () => void }) {
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
      <td className={`chain-click ${stale ? "muted" : ""}`} onClick={onClick} title="add as leg (price = mid)">
        {fmt(side.bid)}
      </td>
      <td className={`chain-click ${stale ? "muted" : ""}`} onClick={onClick} title="add as leg (price = mid)">
        {fmt(side.ask)}
      </td>
    </>
  );
}
