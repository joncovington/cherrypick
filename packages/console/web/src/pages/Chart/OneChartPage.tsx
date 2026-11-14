import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useFuturesTicker, useTechnicalsChart } from "../../lib/api";
import { useFeatures } from "../../lib/useFeatures";
import { isFeatureOn } from "../../lib/visibility";
import { IntradayView } from "../Intraday/IntradayPage";
import { CASH_INSTRUMENTS, liveEntryFor, liveLabel } from "../Intraday/liveSymbols";
import { TechnicalsView } from "../Morning/ChartPage";
import { chartSymbol, dailyReason, intradayReason, resolveTimeframe, TIMEFRAMES, type Timeframe } from "./chartRoute";

/**
 * Charts → chart: one chart for every symbol. The header picks the symbol (a live chip or any name
 * typed) and the timeframe; the timeframe picks the source, each with its own reader: 1m/5m/15m the
 * live DXLink candles with the Market Profile overlay (`IntradayView`), 1d the technicals package's
 * chart file (`TechnicalsView`). Nothing is merged across the two. A timeframe that cannot draw the
 * symbol stays on the header, disabled, with its reason as its tooltip; a symbol neither can draw
 * gets both reasons in place of the chart.
 */
export function OneChartPage() {
  const [params, setParams] = useSearchParams();
  const symbol = chartSymbol(params.get("symbol"));
  const futures = useFuturesTicker();
  const technicalsOn = isFeatureOn("technicals", useFeatures());
  // The index alone (no symbol): which names have a daily chart.
  const index = useTechnicalsChart();
  const charted = useMemo(
    () => (index.data === undefined ? undefined : new Set(index.data.index.symbols.map((s) => s.symbol))),
    [index.data],
  );

  const futuresEntries = futures.data?.entries ?? [];
  const liveSymbols = [...CASH_INSTRUMENTS, ...futuresEntries].map((e) => liveLabel(e.product));
  const entry = liveEntryFor(symbol, futuresEntries);
  // A future is known only once the contract map is read; until then it is neither live nor not.
  const pending = symbol.startsWith("/") && futures.data === undefined && !futures.isError;
  const live = entry !== undefined;
  const noDaily = dailyReason(symbol, technicalsOn, charted);
  const dailyKnown = noDaily !== null ? false : charted === undefined && technicalsOn ? null : true;
  const tf = resolveTimeframe(params.get("tf"), live, dailyKnown);
  const noIntraday = intradayReason(live, liveSymbols);

  const go = (key: "symbol" | "tf", value: string): void => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: key === "tf" });
  };

  return (
    <div className="page">
      <div className="page-title-row" style={{ flexWrap: "wrap", gap: 8 }}>
        <h1>Chart</h1>
        <SymbolBox liveSymbols={liveSymbols} charted={technicalsOn ? charted : undefined} onPick={(s) => go("symbol", s)} />
        <div className="mode-toggle" role="group" aria-label="live symbols">
          {liveSymbols.map((s) => (
            <button key={s} type="button" className={s === symbol ? "mode-btn active" : "mode-btn"} onClick={() => go("symbol", s)}>
              {s}
            </button>
          ))}
        </div>
        <div className="mode-toggle" role="group" aria-label="timeframe">
          {TIMEFRAMES.map((t) => {
            const why = t === "1d" ? noDaily : noIntraday;
            return (
              <button
                key={t}
                type="button"
                className={t === tf && !pending ? "mode-btn active" : "mode-btn"}
                disabled={why !== null}
                aria-disabled={why !== null}
                title={why === null ? undefined : `${t === "1d" ? "Daily" : "Intraday"}: ${why}`}
                onClick={() => go("tf", t)}
                style={why === null ? undefined : { opacity: 0.45, cursor: "not-allowed" }}
              >
                {t}
              </button>
            );
          })}
        </div>
      </div>
      <ChartBody
        symbol={symbol}
        tf={tf}
        pending={pending}
        entry={entry}
        noDaily={noDaily}
        noIntraday={noIntraday}
        futuresFailed={futures.isError}
      />
    </div>
  );
}

function ChartBody({
  symbol,
  tf,
  pending,
  entry,
  noDaily,
  noIntraday,
  futuresFailed,
}: {
  symbol: string;
  tf: Timeframe;
  pending: boolean;
  entry: ReturnType<typeof liveEntryFor>;
  noDaily: string | null;
  noIntraday: string | null;
  futuresFailed: boolean;
}) {
  if (pending) return <p className="muted">loading…</p>;
  if (tf !== "1d" && entry !== undefined) return <IntradayView entry={entry} period={tf} />;
  if (tf === "1d" && noDaily === null) return <TechnicalsView symbol={symbol} />;
  // Neither view can draw it: say both reasons, so the empty page is explained.
  return (
    <p className="muted">
      Nothing to draw for {symbol}: no intraday chart, {futuresFailed && symbol.startsWith("/") ? "the futures contract map could not be read" : noIntraday};
      no daily chart, {noDaily}.
    </p>
  );
}

/**
 * A box for any name: Enter opens what is typed, and a pick from the suggestions (the live symbols and
 * every name with a daily chart) opens at once. It empties after each pick; the symbol shown is the
 * chart's own heading.
 */
function SymbolBox({
  liveSymbols,
  charted,
  onPick,
}: {
  liveSymbols: readonly string[];
  charted: ReadonlySet<string> | undefined;
  onPick: (symbol: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const known = useMemo(() => new Set([...liveSymbols, ...(charted ?? [])]), [liveSymbols, charted]);
  const pick = (s: string): void => {
    onPick(s.trim().toUpperCase());
    setDraft("");
  };
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (draft.trim()) pick(draft);
      }}
    >
      <input
        className="chip review-session-select"
        list="chart-symbols"
        value={draft}
        placeholder="symbol"
        aria-label="Symbol"
        onChange={(e) => {
          const v = e.target.value;
          // A pick from the suggestions arrives as a replacement (or, in some browsers, as a plain
          // Event); a keystroke arrives as insertText. Only a pick navigates, or typing "A" on the way
          // to "AAPL" would open A's chart.
          const inputType = (e.nativeEvent as InputEvent).inputType;
          const picked = inputType === undefined || inputType === "insertReplacementText";
          if (picked && known.has(v.toUpperCase())) pick(v);
          else setDraft(v);
        }}
      />
      <datalist id="chart-symbols">
        {[...known].map((s) => (
          <option key={s} value={s} />
        ))}
      </datalist>
    </form>
  );
}
