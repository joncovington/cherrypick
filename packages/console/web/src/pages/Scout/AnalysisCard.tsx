/**
 * The prose half of the symbol page: a scan-classification headline, one concrete price-action
 * observation, and the supporting bullets.
 *
 * Every line here is generated server-side from a *detected condition* with its numbers inline
 * (see `analytics/narrative.ts`), so each claim is checkable against the chart it sits under. The
 * card is deliberately quiet when nothing was detected — the headline and the bullets are each
 * optional, and an absent one means no setup matched rather than a failure to render.
 */
import { useQuery } from "@tanstack/react-query";

export interface SymbolAnalysisPayload {
  symbol: string;
  headline: { scan: string; text: string } | null;
  priceAction: string | null;
  bullets: string[];
  ivIndex: number | null;
  realizedVol: number | null;
  ivRank: number | null;
  earningsDate: string | null;
  stale: boolean;
}

export function useSymbolNarrative(symbol: string) {
  return useQuery<SymbolAnalysisPayload>({
    queryKey: ["symbol-analysis", symbol],
    queryFn: async () => {
      const res = await fetch(`/api/symbol/${symbol}/analysis`);
      if (!res.ok) throw new Error(`analysis: HTTP ${res.status}`);
      return (await res.json()) as SymbolAnalysisPayload;
    },
    retry: false,
    staleTime: 60_000,
  });
}

function pct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(0)}%`;
}

export function AnalysisCard({ symbol }: { symbol: string }) {
  const { data, isLoading, isError } = useSymbolNarrative(symbol);

  return (
    <section className="card">
      <h2>Analysis</h2>
      {isLoading && <span className="skeleton skeleton-text" style={{ width: "70%" }} />}
      {isError && <p className="muted">Analysis unavailable — no candles for {symbol} yet.</p>}
      {data && data.stale && (
        <p className="muted">Not enough price history for {symbol} to say anything worth saying.</p>
      )}
      {data && !data.stale && (
        <>
          {data.headline && (
            <div style={{ marginBottom: 12 }}>
              <span className="chip">{data.headline.scan}</span>
              <p style={{ margin: "8px 0 0" }}>{data.headline.text}</p>
            </div>
          )}
          {data.priceAction && (
            <>
              <div className="fine-label">Price action</div>
              <p style={{ margin: "4px 0 12px" }}>{data.priceAction}</p>
            </>
          )}
          {data.bullets.length > 0 && (
            <ul style={{ margin: "0 0 12px", paddingLeft: 18 }}>
              {data.bullets.map((b) => (
                <li key={b} style={{ marginBottom: 4 }}>
                  {b}
                </li>
              ))}
            </ul>
          )}
          {/* What the IV-vs-realized bullet was judged against, so the reader can check it. */}
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>
            IV {pct(data.ivIndex)} · realized {pct(data.realizedVol)} · IV rank{" "}
            {data.ivRank === null ? "—" : (data.ivRank * 100).toFixed(0)}
            {data.earningsDate ? ` · earnings ${data.earningsDate}` : ""}
          </p>
        </>
      )}
    </section>
  );
}
