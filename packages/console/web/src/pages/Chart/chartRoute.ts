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

/**
 * A link to the one chart, the only place the console spells one: `chartHref("MSFT", "1d")` for a
 * name's daily chart, `chartHref("SPX", "15m", { day })` for a session's letters under the chart.
 * Extra keys are the view's own (setup, side, day, ...) and follow in the order given.
 */
export function chartHref(symbol: string, tf: Timeframe, extra: Record<string, string> = {}): string {
  const q = new URLSearchParams({ symbol, tf, ...extra });
  // A slash is legal in a query; left encoded, "/ES" would read "%2FES" in the address bar.
  return `/charts/chart?${q.toString().replace(/%2F/g, "/")}`;
}

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

/** A retired Charts tab whose links and bookmarks still arrive. */
export type MovedChartTab = "intraday" | "technicals" | "profile";

/**
 * An old chart URL on the one chart, its query carried. `/charts/intraday?product=ES&period=15m`
 * names the live chart's product and width; `/charts/technicals?symbol=X` the daily chart, whose
 * symbol defaulted to SPX; `/charts/profile?symbol=SPX&sessions=10` the recorded sessions, now the
 * sessions panel under the 15-minute chart (the width that reaches furthest back), its `sessions`
 * becoming the panel's `days` (the chart's own `sessions` counts histograms). Every other key
 * (profile, hide, show, setup, side, levels, composite, measure, day) is the view's own and goes as it is.
 */
export function movedChartUrl(from: MovedChartTab, search: string): string {
  const old = new URLSearchParams(search);
  const next = new URLSearchParams();
  const renamed = new Set(["product", "period", "symbol", "tf"]);
  if (from === "intraday") {
    next.set("symbol", liveLabel((old.get("product") ?? "ES").toUpperCase()));
    const period = old.get("period");
    if (isTimeframe(period) && period !== "1d") next.set("tf", period);
  } else if (from === "technicals") {
    next.set("symbol", chartSymbol(old.get("symbol") ?? "SPX"));
    next.set("tf", "1d");
  } else {
    next.set("symbol", chartSymbol(old.get("symbol") ?? "SPX"));
    next.set("tf", "15m");
    renamed.add("sessions");
    const days = old.get("sessions");
    if (days !== null) next.set("days", days);
  }
  for (const [k, v] of old) if (!renamed.has(k)) next.append(k, v);
  // A slash is legal in a query; left encoded, "/ES" would read "%2FES" in the address bar.
  return `/charts/chart?${next.toString().replace(/%2F/g, "/")}`;
}
