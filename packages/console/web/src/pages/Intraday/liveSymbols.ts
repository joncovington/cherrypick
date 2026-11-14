import type { FuturesTickerEntry } from "@console/shared";

/**
 * The symbols the live intraday chart draws, and how the Charts page spells them. Kept apart from
 * `IntradayPage.tsx` on purpose: that module reads the theme's CSS variables at import time, and the
 * app shell imports these helpers (through the chart redirects) before the stylesheet has loaded.
 */

/** Cash symbols the chart offers beside the futures, profiled over the same regular session. An
 *  index (SPX, NDX) carries no volume, so its profile is TPO only. */
export const CASH_INSTRUMENTS: FuturesTickerEntry[] = [
  { product: "SPX", label: "SPX", name: "S&P 500 index", contract: null, streamerSymbol: "SPX", expiration: null, reason: null },
  { product: "SPY", label: "SPY", name: "S&P 500 ETF", contract: null, streamerSymbol: "SPY", expiration: null, reason: null },
  { product: "NDX", label: "NDX", name: "Nasdaq-100 index", contract: null, streamerSymbol: "NDX", expiration: null, reason: null },
  { product: "QQQ", label: "QQQ", name: "Nasdaq-100 ETF", contract: null, streamerSymbol: "QQQ", expiration: null, reason: null },
];

export const isCash = (product: string): boolean => CASH_INSTRUMENTS.some((e) => e.product === product);

/** A live product as the Charts page spells its symbol: a cash symbol as itself, a future with a slash
 *  ("ES" → "/ES"), the profile store's spelling. */
export const liveLabel = (product: string): string => (isCash(product) ? product : `/${product}`);

/** The live entry a Charts symbol names ("/ES" → the ES future, "SPX" → the cash index), or undefined
 *  for a symbol the feed does not carry. `futures` is the futures ticker's map. */
export function liveEntryFor(symbol: string, futures: readonly FuturesTickerEntry[]): FuturesTickerEntry | undefined {
  return [...CASH_INSTRUMENTS, ...futures].find((e) => liveLabel(e.product) === symbol);
}
