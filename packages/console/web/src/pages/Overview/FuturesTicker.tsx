import { useEffect, useRef } from "react";
import type { FuturesTickerEntry } from "@console/shared";
import { useFuturesTicker } from "../../lib/api";
import { useQuote } from "../../lib/useQuote";

/**
 * The Overview's futures ticker: /ES /NQ /CL /GC /ZB at their live price, flashing on each tick.
 *
 * The contract for each product comes from the server (`state/futures_contracts.json`, the broker's
 * own answer, never assembled here); the prices ride the ordinary quote socket, so a contract the
 * streamer does not carry still quotes through the console's own DXLink session. A cached price
 * (DXLink down) renders muted, and an unresolved contract renders a dash with the reason on hover.
 */

const REASONS: Record<string, string> = {
  no_map: "no contract map — scripts/refresh_futures_contracts.py has not run",
  map_stale: "contract map is over five days old and may name a rolled contract",
  not_in_map: "this product is not in the contract map yet — the next contract refresh adds it",
};

/** Treasury bond futures are quoted in 32nds: 115.5 prints as 115'16. */
function thirtySeconds(v: number): string {
  let whole = Math.floor(v);
  let ticks = Math.round((v - whole) * 32);
  if (ticks === 32) {
    whole += 1;
    ticks = 0;
  }
  return `${String(whole)}'${String(ticks).padStart(2, "0")}`;
}

function formatPrice(product: string, v: number): string {
  if (product === "ZB") return thirtySeconds(v);
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function Tick({ entry }: { entry: FuturesTickerEntry }) {
  const q = useQuote(entry.streamerSymbol ?? "");
  const priceRef = useRef<HTMLSpanElement>(null);
  const prevTs = useRef<number>(0);

  useEffect(() => {
    if (q === undefined || q.ts === prevTs.current) return;
    prevTs.current = q.ts;
    const el = priceRef.current;
    if (el === null || q.direction === null || q.source !== "dxlink") return;
    el.classList.remove("flash-up", "flash-down");
    void el.offsetWidth; // restart the animation
    el.classList.add(q.direction === "up" ? "flash-up" : "flash-down");
  }, [q]);

  const price =
    q?.last ?? (q?.bid !== undefined && q?.ask !== undefined ? (q.bid + q.ask) / 2 : undefined);
  const cached = q !== undefined && q.source !== "dxlink";
  const tone = cached ? "muted" : q?.direction === "up" ? "pnl-pos" : q?.direction === "down" ? "pnl-neg" : "";

  const title =
    entry.reason !== null
      ? `${entry.name}: ${REASONS[entry.reason] ?? entry.reason}`
      : `${entry.name} · ${entry.contract ?? entry.streamerSymbol ?? ""}` +
        (entry.expiration !== null ? ` · expires ${entry.expiration}` : "") +
        (price !== undefined ? ` · ${price.toString()}` : "") +
        (cached ? " · cached (live feed down)" : "");

  return (
    <span className="futures-tick" title={title}>
      <span className="futures-tick-label">{entry.label}</span>
      <span ref={priceRef} className={`futures-tick-price ${tone}`}>
        {price !== undefined ? formatPrice(entry.product, price) : "—"}
      </span>
    </span>
  );
}

export function FuturesTicker() {
  const { data } = useFuturesTicker();
  if (data === undefined) return null;
  return (
    <div className="futures-ticker" aria-label="futures ticker">
      {data.entries.map((e) => (
        <Tick key={e.product} entry={e} />
      ))}
    </div>
  );
}
