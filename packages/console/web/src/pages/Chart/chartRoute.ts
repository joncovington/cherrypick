import { CANDLE_PERIODS } from "@console/shared";
import { liveLabel } from "../Intraday/liveSymbols";

/**
 * The Charts page's one chart, as its URL states it: `/charts/chart?symbol=/ES&tf=5m`. The symbol is
 * the profile store's spelling (a future with its slash, a cash symbol or stock as itself); the
 * timeframe picks the source, the live feed for 1m/5m/15m and the technicals chart file for 1d. These
 * are the page's rules, kept pure so a test can hold them.
 */

export const TIMEFRAMES = [...CANDLE_PERIODS, "1d"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

/** What the chart opens on when the URL names no symbol. */
export const DEFAULT_SYMBOL = "/ES";
/** The width a live symbol opens on when the URL names none. */
export const DEFAULT_INTRADAY: Timeframe = "5m";

const isTimeframe = (raw: string | null): raw is Timeframe => raw !== null && (TIMEFRAMES as readonly string[]).includes(raw);

/** `?symbol=` read back: trimmed, upper case, the default when empty. */
export function chartSymbol(raw: string | null): string {
  const s = (raw ?? "").trim().toUpperCase();
  return s === "" ? DEFAULT_SYMBOL : s;
}

/**
 * The timeframe drawn. An intraday width is kept while the feed carries the symbol; the daily view is
 * kept unless the symbol is known to have no daily chart (`daily` false; null while that is still being
 * read). Anything else falls to the symbol's own default: a live symbol opens on 5m, any other on daily.
 */
export function resolveTimeframe(raw: string | null, live: boolean, daily: boolean | null): Timeframe {
  if (isTimeframe(raw)) {
    if (raw !== "1d" && live) return raw;
    if (raw === "1d" && daily !== false) return raw;
  }
  return live ? DEFAULT_INTRADAY : "1d";
}

/**
 * Why the daily view cannot draw a symbol, or null when it can (or while the index is still being
 * read: `charted` undefined). The daily view is the technicals package's chart file, so it follows that
 * feature's switch and holds only the names the report job charts; a future has none.
 */
export function dailyReason(symbol: string, technicalsOn: boolean, charted: ReadonlySet<string> | undefined): string | null {
  if (!technicalsOn) return "the daily chart is the technicals package's, and technicals is off in the suite config";
  if (symbol.startsWith("/")) return `the technicals package charts stocks, ETFs and SPX, not futures such as ${symbol}`;
  if (charted !== undefined && !charted.has(symbol)) return `the technicals package has no daily chart for ${symbol}`;
  return null;
}

/** Why the intraday widths cannot draw a symbol, or null when the feed carries it. */
export function intradayReason(live: boolean, liveSymbols: readonly string[]): string | null {
  return live ? null : `the live feed carries ${liveSymbols.join(", ")} only`;
}

/**
 * An old chart URL on the one chart, its query carried. `/charts/intraday?product=ES&period=15m`
 * names the live chart's product and width; `/charts/technicals?symbol=X` the daily chart, whose
 * symbol defaulted to SPX. Every other key (profile, sessions, hide, show, setup, side, levels) is
 * the view's own and goes as it is.
 */
export function movedChartUrl(from: "intraday" | "technicals", search: string): string {
  const old = new URLSearchParams(search);
  const next = new URLSearchParams();
  if (from === "intraday") {
    next.set("symbol", liveLabel((old.get("product") ?? "ES").toUpperCase()));
    const period = old.get("period");
    if (isTimeframe(period) && period !== "1d") next.set("tf", period);
  } else {
    next.set("symbol", chartSymbol(old.get("symbol") ?? "SPX"));
    next.set("tf", "1d");
  }
  for (const [k, v] of old) if (!["product", "period", "symbol", "tf"].includes(k)) next.append(k, v);
  // A slash is legal in a query; left encoded, "/ES" would read "%2FES" in the address bar.
  return `/charts/chart?${next.toString().replace(/%2F/g, "/")}`;
}
