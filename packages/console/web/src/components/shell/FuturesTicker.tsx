import { useEffect, useRef } from "react";
import type { FuturesTickerEntry } from "@console/shared";
import { useFuturesTicker } from "../../lib/api";
import { useQuote } from "../../lib/useQuote";
import type { QuoteState } from "../../lib/wsClient";

/**
 * The top bar's futures ticker: /ES /NQ /CL /GC /ZB as chips, each with its live price and its
 * percentage change on the day, green when up and red when down, flashing on every tick. The change
 * in points is on hover: five chips with both did not fit beside the clock at 1500px.
 *
 * The contract for each product comes from the server (`state/futures_contracts.json`, the broker's
 * own answer, never assembled here); the prices ride the ordinary quote socket, so a contract the
 * streamer does not carry still quotes through the console's own DXLink session. The change is
 * against the feed's own prior settle (DXLink's Summary event) and is only drawn for a live price:
 * a cached price (DXLink down) renders muted with no change, rather than a change measured between
 * two numbers from different sources. An unresolved contract renders a dash with the reason on hover.
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

export function formatPrice(product: string, v: number): string {
  if (product === "ZB") return thirtySeconds(v);
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function formatPct(v: number): string {
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}%`;
}

export function formatChange(product: string, v: number): string {
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${sign}${formatPrice(product, Math.abs(v))}`;
}

export interface ChipState {
  price: number | undefined;
  /** Against the feed's prior settle; undefined unless the price is the feed's own. */
  change: number | undefined;
  changePct: number | undefined;
  tone: "" | "futures-chip-up" | "futures-chip-down" | "futures-chip-cached";
}

/** What a chip shows for a quote. Pure, so the colour rule is testable without a socket. */
export function chipState(q: QuoteState | undefined): ChipState {
  const price =
    q?.last ?? (q?.bid !== undefined && q?.ask !== undefined ? (q.bid + q.ask) / 2 : undefined);
  const live = q?.source === "dxlink";
  const change =
    live && price !== undefined && q?.prevClose !== undefined ? price - q.prevClose : undefined;
  const changePct =
    change !== undefined && q?.prevClose !== undefined && q.prevClose !== 0
      ? (change / q.prevClose) * 100
      : undefined;
  const tone =
    q !== undefined && !live
      ? "futures-chip-cached"
      : change === undefined || change === 0
        ? ""
        : change > 0
          ? "futures-chip-up"
          : "futures-chip-down";
  return { price, change, changePct, tone };
}

function Tick({ entry }: { entry: FuturesTickerEntry }) {
  const q = useQuote(entry.streamerSymbol ?? "");
  const chipRef = useRef<HTMLSpanElement>(null);
  const prevTs = useRef<number>(0);

  useEffect(() => {
    if (q === undefined || q.ts === prevTs.current) return;
    prevTs.current = q.ts;
    const el = chipRef.current;
    if (el === null || q.direction === null || q.source !== "dxlink") return;
    el.classList.remove("flash-up", "flash-down");
    void el.offsetWidth; // restart the animation
    el.classList.add(q.direction === "up" ? "flash-up" : "flash-down");
  }, [q]);

  const { price, change, changePct, tone } = chipState(q);
  const live = q?.source === "dxlink";

  const title =
    entry.reason !== null
      ? `${entry.name}: ${REASONS[entry.reason] ?? entry.reason}`
      : [
          `${entry.name} · ${entry.contract ?? entry.streamerSymbol ?? ""}`,
          entry.expiration !== null ? `expires ${entry.expiration}` : null,
          change !== undefined ? `${formatChange(entry.product, change)} on the day` : null,
          q?.prevClose !== undefined ? `prior settle ${formatPrice(entry.product, q.prevClose)}` : null,
          q !== undefined && !live ? "cached price (live feed down), no change shown" : null,
        ]
          .filter((s) => s !== null)
          .join(" · ");

  return (
    <span ref={chipRef} className={`chip futures-chip ${tone}`} title={title}>
      <span className="futures-chip-symbol">{entry.label}</span>
      <span className="futures-chip-price">{price !== undefined ? formatPrice(entry.product, price) : "—"}</span>
      {changePct !== undefined && <span className="futures-chip-change">{formatPct(changePct)}</span>}
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
