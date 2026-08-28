import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useGex, useGexIntegrity } from "../../lib/api";
import { DataCard, fmtNum } from "../../components/DataTable";
import { IntegrityStrip } from "./IntegrityStrip";
import { Pager, TabStrip, usePage } from "../../components/ScopeBar";
import { useFlashOnChange } from "../../lib/useFlashOnChange";
import { GexProfileChart, IvSkewChart, StrikeBarsChart, fmtGexDollars, type GexStrikeRow, type GexView } from "./GexProfileChart";

interface GexProfilePayload {
  ok: boolean;
  error?: string;
  symbol?: string;
  spot?: number;
  spotUpdatedAt?: number | null;
  expiration?: string;
  series?: GexStrikeRow[];
  totals?: {
    total_call_gex: number;
    total_put_gex: number;
    net_gex: number;
    max_gex_strike: number | null;
    zero_gamma: number | null;
    call_wall: number | null;
    put_wall: number | null;
  };
  spotHistory?: Array<{ ts: number; spot: number }>;
  spotSession?: { date: string; openTs: number; closeTs: number } | null;
  volumeTotals?: {
    total_call_gex_vol: number;
    total_put_gex_vol: number;
    net_gex_vol: number;
    zero_gamma_vol: number | null;
    call_wall_vol: number | null;
    put_wall_vol: number | null;
  };
}

function useGexProfile(symbol: string, enabled: boolean) {
  return useQuery<GexProfilePayload>({
    queryKey: ["gex-profile", symbol],
    queryFn: async () => {
      const res = await fetch(`/api/gex/profile/${symbol}`);
      if (!res.ok) throw new Error(`gex profile: HTTP ${res.status}`);
      return (await res.json()) as GexProfilePayload;
    },
    refetchInterval: 15_000,
    enabled,
  });
}

function useGexSymbols() {
  return useQuery<{ symbols: string[] }>({
    queryKey: ["gex-symbols"],
    queryFn: async () => {
      const res = await fetch("/api/gex/symbols");
      if (!res.ok) throw new Error("gex symbols failed");
      return (await res.json()) as { symbols: string[] };
    },
    staleTime: 300_000,
  });
}

function Metric({
  label,
  value,
  colored,
  tone,
  emphasis,
  flashValue,
}: {
  label: string;
  value: string;
  /** Color by this number's sign. */
  colored?: number;
  /** Fixed semantic tone: call-side green, put-side red. */
  tone?: "pos" | "neg";
  /** Slightly larger row for headline metrics (net GEX). */
  emphasis?: boolean;
  /** Raw number to flash on when it changes tick to tick -- omit for metrics with no clean single
      numeric value to compare (e.g. a formatted string with no backing number passed through). */
  flashValue?: number | null;
}) {
  const cls =
    tone !== undefined
      ? tone === "pos"
        ? "pnl-pos"
        : "pnl-neg"
      : colored === undefined
        ? ""
        : colored >= 0
          ? "pnl-pos"
          : "pnl-neg";
  const flashRef = useFlashOnChange<HTMLSpanElement>(flashValue);
  return (
    <div className={`gex-metric ${emphasis === true ? "gex-metric-em" : ""}`}>
      <span className="muted">{label}</span>
      <span ref={flashRef} className={`gex-metric-value ${cls}`}>{value}</span>
    </div>
  );
}

function fmtEt(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false });
}

function fmtGex(v: number | null): string {
  if (v === null) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  return v.toFixed(0);
}

type GexTab = "gex" | "skew" | "volume" | "history";

const TABS: Array<[GexTab, string]> = [
  ["gex", "GEX"],
  ["skew", "IV Skew"],
  ["volume", "Volume"],
  ["history", "History"],
];

export function GexPage() {
  const [symbol, setSymbol] = useState("SPX");
  const [view, setView] = useState<GexView>("oivol");
  const [tab, setTab] = useState<GexTab>("gex");
  // Each query only backs the tab(s) that read it -- the history table needs the overview
  // endpoint, everything else needs the per-symbol profile, and there's no reason to keep
  // polling either one for a tab that isn't on screen.
  const { page, setOffset, setLimit } = usePage();
  const { data, isLoading, isError } = useGex(tab === "history", page);
  // Its own tiny query: `useGex` is gated to the history tab, and a stale flip is precisely what a
  // reader of the chart tab needs to know.
  const integrity = useGexIntegrity();
  const symbols = useGexSymbols();
  const profile = useGexProfile(symbol, tab !== "history");
  const p = profile.data;

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>GEX</h1>
        {symbols.data && (
          <select className="text-input" value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="symbol">
            {symbols.data.symbols.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        )}
        {p?.ok && (
          <span className="chip">
            exp {p.expiration} · spot {p.spot?.toFixed(2)}
          </span>
        )}
        {p?.ok && p.spotUpdatedAt != null && (
          <span className="chip muted" title="when the underlying's cached spot last updated -- the age of everything on this tab">
            as of {new Date(p.spotUpdatedAt * 1000).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false })} ET
          </span>
        )}
        <TabStrip
          tabs={TABS.map(([t]) => t)}
          value={tab}
          onChange={setTab}
          labels={Object.fromEntries(TABS) as Partial<Record<GexTab, string>>}
          ariaLabel="gex tabs"
        />
        {tab === "gex" && (
          <div className="mode-toggle" style={{ marginLeft: 0 }} role="group" aria-label="chart view">
            {(["oivol", "net", "abs"] as GexView[]).map((v) => (
              <button key={v} type="button" className={view === v ? "mode-btn active" : "mode-btn"} onClick={() => setView(v)}>
                {v === "oivol" ? "OI vs Vol" : v === "net" ? "Net" : "Abs"}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Above the numbers it qualifies, on every tab. This module journals no measurement breaks;
          what makes a flip or a wall untrustworthy is staleness and truncation, and both have bitten. */}
      <IntegrityStrip data={integrity.data} updatedAt={integrity.dataUpdatedAt} />

      <div className="cards cards-wide">
        {tab === "gex" && (
        <section className="card view-fade">
          <h2>
            GEX by strike — {view === "net" ? "net (green = call heavy, red = put heavy)" : view === "oivol" ? "net GEX, OI vs volume" : "absolute"}
          </h2>
          {p?.ok && p.series && p.spot !== undefined && p.totals ? (
            <div className="gex-layout">
              <div className="gex-chart-area">
                {/* Overlays always use the VOLUME basis, like the old page's plugin:
                    flow-based zero gamma and walls track today's session, and they
                    sit near spot where the strike window actually is. */}
                <GexProfileChart
                  series={p.series}
                  view={view}
                  spot={p.spot}
                  zeroGamma={p.volumeTotals?.zero_gamma_vol ?? null}
                  callWall={p.volumeTotals?.call_wall_vol ?? null}
                  putWall={p.volumeTotals?.put_wall_vol ?? null}
                  spotHistory={p.spotHistory}
                  spotSession={p.spotSession}
                />
              </div>
              <div className="gex-rail">
                <div className="gex-panel">
                  <h2>open interest (positioning)</h2>
                  <Metric label="total call GEX" value={fmtGexDollars(p.totals.total_call_gex)} tone="pos" />
                  <Metric label="total put GEX" value={fmtGexDollars(-p.totals.total_put_gex)} tone="neg" />
                  <Metric label="net GEX" value={fmtGexDollars(p.totals.net_gex)} colored={p.totals.net_gex} flashValue={p.totals.net_gex} emphasis />
                  <Metric label="max GEX strike" value={String(p.totals.max_gex_strike ?? "—")} />
                  <Metric label="call wall" value={String(p.totals.call_wall ?? "—")} tone="pos" />
                  <Metric label="put wall" value={String(p.totals.put_wall ?? "—")} tone="neg" />
                  <Metric label="zero gamma (flip)" value={p.totals.zero_gamma !== null ? p.totals.zero_gamma.toFixed(0) : "—"} />
                </div>
                <div className="gex-panel">
                  <h2>volume (flow)</h2>
                  <Metric label="total call GEX" value={fmtGexDollars(p.volumeTotals?.total_call_gex_vol ?? 0)} tone="pos" />
                  <Metric label="total put GEX" value={fmtGexDollars(-(p.volumeTotals?.total_put_gex_vol ?? 0))} tone="neg" />
                  <Metric label="net GEX" value={fmtGexDollars(p.volumeTotals?.net_gex_vol ?? 0)} colored={p.volumeTotals?.net_gex_vol} flashValue={p.volumeTotals?.net_gex_vol} emphasis />
                  <Metric label="call wall" value={String(p.volumeTotals?.call_wall_vol ?? "—")} tone="pos" />
                  <Metric label="put wall" value={String(p.volumeTotals?.put_wall_vol ?? "—")} tone="neg" />
                  <Metric label="zero gamma" value={p.volumeTotals?.zero_gamma_vol != null ? p.volumeTotals.zero_gamma_vol.toFixed(0) : "—"} />
                </div>
              </div>
            </div>
          ) : profile.isLoading ? (
            <span className="skeleton skeleton-text" style={{ width: "50%" }} />
          ) : (
            <p className="muted">{p?.error ?? "no profile"}</p>
          )}
        </section>
        )}

        {tab === "skew" && (
        <>
        <section className="card view-fade">
          <h2>IV skew — call vs put by strike</h2>
          {p?.ok && p.series && p.spot !== undefined ? (
            <IvSkewChart series={p.series} spot={p.spot} />
          ) : (
            <span className="skeleton skeleton-text" style={{ width: "40%" }} />
          )}
        </section>

        <section className="card view-fade">
          <h2>Open interest by strike (calls right, puts left; volume lighter)</h2>
          {p?.ok && p.series && p.spot !== undefined ? (
            <StrikeBarsChart
              series={p.series}
              spot={p.spot}
              bars={[
                { label: "call OI", color: "#43b57a", value: (r) => r.call_oi },
                { label: "put OI", color: "#d95c4a", value: (r) => -r.put_oi },
                { label: "call vol", color: "#7fd4a8", value: (r) => r.call_vol },
                { label: "put vol", color: "#e89386", value: (r) => -r.put_vol },
              ]}
            />
          ) : (
            <span className="skeleton skeleton-text" style={{ width: "40%" }} />
          )}
        </section>
        </>
        )}

        {tab === "volume" && (
        <section className="card view-fade">
          <h2>Volume by strike (calls vs puts)</h2>
          {p?.ok && p.series && p.spot !== undefined ? (
            <StrikeBarsChart
              series={p.series}
              spot={p.spot}
              bars={[
                { label: "call vol", color: "#43b57a", value: (r) => r.call_vol },
                { label: "put vol", color: "#d95c4a", value: (r) => -r.put_vol },
              ]}
            />
          ) : (
            <span className="skeleton skeleton-text" style={{ width: "40%" }} />
          )}
        </section>
        )}

        {tab === "history" && (
        <>
        <DataCard
          title="Latest regime per symbol"
          headers={["sym", "as of", "spot", "net GEX", "net GEX (vol)", "zero gamma", "call wall", "put wall"]}
          numFrom={1}
          loading={isLoading}
          isError={isError}
          rowCount={data?.latest.length ?? 0}
          skeletonRows={3}
          className="view-fade"
        >
          {data?.latest.map((g) => (
            <tr key={g.symbol}>
              <td>{g.symbol}</td>
              <td className="muted">{fmtEt(g.ts)}</td>
              <td>{fmtNum(g.spot, 2)}</td>
              <td>{fmtGex(g.netGex)}</td>
              <td>{fmtGex(g.netGexVol)}</td>
              <td>{fmtNum(g.zeroGamma, 0)}</td>
              <td>{fmtNum(g.callWall, 0)}</td>
              <td>{fmtNum(g.putWall, 0)}</td>
            </tr>
          ))}
        </DataCard>

        <DataCard
          title="Today's regime snapshots"
          headers={["time", "sym", "spot", "net GEX", "zero gamma", "call wall", "put wall"]}
          numFrom={1}
          loading={isLoading}
          isError={isError}
          rowCount={data?.recent.rows.length ?? 0}
          skeletonRows={10}
          className="view-fade"
          footer={
            data !== undefined && (
              <Pager
                offset={data.recent.offset}
                limit={data.recent.limit}
                total={data.recent.total}
                onOffset={setOffset}
                onLimit={setLimit}
              />
            )
          }
        >
          {data?.recent.rows.map((g, i) => (
            <tr key={`${g.symbol}-${g.ts}-${i}`}>
              <td className="muted">{fmtEt(g.ts)}</td>
              <td>{g.symbol}</td>
              <td>{fmtNum(g.spot, 2)}</td>
              <td>{fmtGex(g.netGex)}</td>
              <td>{fmtNum(g.zeroGamma, 0)}</td>
              <td>{fmtNum(g.callWall, 0)}</td>
              <td>{fmtNum(g.putWall, 0)}</td>
            </tr>
          ))}
        </DataCard>
        </>
        )}
      </div>
    </div>
  );
}
