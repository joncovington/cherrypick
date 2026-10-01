/**
 * The futures ticker on the Overview: which contract each product is today. The contract is never
 * assembled here — it comes from `state/futures_contracts.json`, which
 * `scripts/refresh_futures_contracts.py` writes from the broker's instruments endpoint, the same map
 * the gex recorder and the morning pack read. Prices ride the ordinary quote socket.
 */
export interface FuturesTickerEntry {
  /** Product code: ES, NQ, CL, GC, ZB. */
  product: string;
  /** What the ticker prints: "/ES". */
  label: string;
  /** What it is, for the hover: "S&P 500 e-mini". */
  name: string;
  /** The contract, e.g. "/ESZ6"; null when the map does not resolve it. */
  contract: string | null;
  /** The DXLink symbol to subscribe, e.g. "/ESZ26:XCME"; null whenever `reason` is set. */
  streamerSymbol: string | null;
  expiration: string | null;
  /** Why there is no symbol: `no_map`, `map_stale` or `not_in_map`. */
  reason: string | null;
}

export interface FuturesTickerPayload {
  /** When the contract map was last refreshed; null when there is none. */
  refreshedAt: string | null;
  entries: FuturesTickerEntry[];
}
