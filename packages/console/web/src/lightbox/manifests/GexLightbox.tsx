import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { GexPayload } from "@console/shared";
import { useGex, useGexIntegrity } from "../../lib/api";
import { fmtNum } from "../../components/DataTable";
import { Spark } from "../../components/chart/Spark";
import { IntegrityStrip, gexHasAttention } from "../../pages/Gex/IntegrityStrip";
import { useFlashOnChange } from "../../lib/useFlashOnChange";
import {
  GexProfileChart,
  IvSkewChart,
  StrikeBarsChart,
  fmtGexDollars,
  type GexStrikeRow,
  type GexView,
} from "../../pages/Gex/GexProfileChart";
import { ModuleFrame } from "../ModuleFrame";
import { GEX_SLIDES, type GexSlideId } from "../navGroups";
import type { SlideDef } from "../types";

interface GexProfilePayload {
  ok: boolean;
  error?: string;
  symbol?: string;
  spot?: number;
  spotUpdatedAt?: number | null;
  expiration?: string;
  /** "last_session" off-hours: the chart is the last session's chain, as of `chainAsOf`. */
  sessionMode?: "live" | "last_session";
  chainAsOf?: number | null;
  leftoverRowsDropped?: number;
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
  /** A runner-up within 20% of a wall, per basis (server `contestedWalls`); null when the wall is clear. */
  wallAlternates?: Record<"oi" | "vol", Record<"call" | "put", { strike: number; strength: number } | null>>;
}

/**
 * A wall, with its runner-up when the two are nearly tied: "7740 / 7600". One number reads as a
 * level; on a near-tie it hops between the two on noise (52 of 53 recorded put-wall hops), so the
 * honest read is both.
 */
export function fmtWall(wall: number | null | undefined, alt: { strike: number } | null | undefined): string {
  if (wall === null || wall === undefined) return "—";
  return alt ? `${wall} / ${alt.strike}` : String(wall);
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
  colored?: number;
  tone?: "pos" | "neg";
  emphasis?: boolean;
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

const ET = "America/New_York";

function etDay(d: Date): string {
  return d.toLocaleDateString("en-US", { timeZone: ET });
}

/** Today's time, or the ET date of an older recording, so a stale symbol never reads as current. */
export function fmtRecorded(iso: string, now: Date = new Date()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const d = new Date(t);
  if (etDay(d) === etDay(now)) return d.toLocaleTimeString("en-US", { timeZone: ET, hour: "2-digit", minute: "2-digit", hour12: false });
  return d.toLocaleDateString("en-CA", { timeZone: ET });
}

export function asOfIsToday(epochSeconds: number): boolean {
  return etDay(new Date(epochSeconds * 1000)) === etDay(new Date());
}

/**
 * The cached spot's timestamp, DATED whenever it is not from today.
 *
 * A bare time reads as "now". The shared stream cache retains chains for symbols the suite
 * retired weeks ago, and a profile built from one rendered "as of 20:14 ET" -- indistinguishable
 * from tonight. The rule this page inherits is that a stale reading must announce itself: an old
 * row does not, it just reads as live (the lesson `positions.py` records for its own marks).
 */
export function formatAsOf(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  const time = d.toLocaleTimeString("en-US", { timeZone: ET, hour12: false });
  return asOfIsToday(epochSeconds) ? time : `${etDay(d)} ${time}`;
}

/** The whole of today's recorded regime, not a page of it: one sparkline wants every point. */
const DAY_PAGE = { offset: 0, limit: 500 };

/**
 * Net GEX as the recorder wrote it through today, for the chosen symbol, and when each symbol was
 * last recorded. It replaced the `history` tab's two tables (2026-10-30): a 77-row snapshot table
 * read as one line on a chart, and the latest-per-symbol table was mostly a staleness check.
 */
export function RecordedToday({ symbol, data }: { symbol: string; data: GexPayload | undefined }) {
  if (data === undefined) return null;
  const rows = data.recent.rows.filter((r) => r.symbol === symbol && r.netGex !== null).reverse();
  const first = rows[0];
  const last = rows[rows.length - 1];
  const capped = data.recent.total > data.recent.rows.length;
  return (
    <section className="card view-fade">
      <div className="card-head">
        <h2>Net GEX recorded today — {symbol}</h2>
        {first !== undefined && last !== undefined && (
          <span className="card-asof">
            {rows.length} snapshots, {fmtEt(first.ts)} to {fmtEt(last.ts)}
            {capped ? ` (the day's first ${String(data.recent.rows.length)} of ${String(data.recent.total)} rows)` : ""}
          </span>
        )}
      </div>
      {rows.length >= 2 ? (
        <>
          <Spark values={rows.map((r) => r.netGex!)} height={48} tone="sign" title="net GEX through the day" />
          <p className="muted">
            {fmtGex(first!.netGex)} at the first snapshot, {fmtGex(last!.netGex)} at the last; zero gamma {fmtNum(last!.zeroGamma, 0)},
            walls {fmtNum(last!.putWall, 0)} / {fmtNum(last!.callWall, 0)}.
          </p>
        </>
      ) : (
        <p className="muted">The recorder has written {rows.length === 0 ? "nothing" : "one snapshot"} for {symbol} today.</p>
      )}
      <p className="muted">
        Last recorded:{" "}
        {data.latest.map((g, i) => (
          <span key={g.symbol}>
            {i > 0 ? " · " : ""}
            {g.symbol} {fmtRecorded(g.ts)}
          </span>
        ))}
      </p>
    </section>
  );
}

function isGexSlideId(v: string): v is GexSlideId {
  return GEX_SLIDES.some((s) => s.id === v);
}

/**
 * GEX on the module frame (2026-09-25; a lightbox before that). Its former page tabs
 * (GEX/IV Skew/Volume/History) are its pages, declared in `navGroups.ts`, and the integrity strip --
 * which used to sit above every tab's cards on the standalone page -- rides the same chip-plus-
 * drawer slot every other module manifest uses (`integrity`/`integrityAttention`), collapsed by
 * default rather than always spending body height on it; the by-strike chart is bounded to fit the
 * body without scrolling (`.gex-chart-fit` in `GexProfileChart.tsx`), which the strip's own height
 * would otherwise have crowded.
 */
export function GexLightbox({ slide }: { slide: string }) {
  const [symbol, setSymbol] = useState("SPX");
  const [view, setView] = useState<GexView>("oivol");
  const activeSlide: GexSlideId = isGexSlideId(slide) ? slide : "gex";
  const { data } = useGex(activeSlide === "gex", DAY_PAGE);
  const integrity = useGexIntegrity();
  const symbols = useGexSymbols();
  const profile = useGexProfile(symbol, true);
  const p = profile.data;

  const slides: SlideDef[] = [
    {
      id: "gex",
      label: "gex",
      render: () => (
        <div className="cards cards-wide">
          <section className="card view-fade">
            <h2>
              GEX by strike — {view === "net" ? "net (green = call heavy, red = put heavy)" : view === "oivol" ? "net GEX, OI vs volume" : "absolute"}
            </h2>
            {p?.ok && p.series && p.spot !== undefined && p.totals ? (
              <div className="gex-layout">
                <div className="gex-chart-area">
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
                    <Metric label="call wall" value={fmtWall(p.totals.call_wall, p.wallAlternates?.oi.call)} tone="pos" />
                    <Metric label="put wall" value={fmtWall(p.totals.put_wall, p.wallAlternates?.oi.put)} tone="neg" />
                    <Metric label="zero gamma (flip)" value={p.totals.zero_gamma !== null ? p.totals.zero_gamma.toFixed(0) : "—"} />
                  </div>
                  <div className="gex-panel">
                    <h2>volume (flow)</h2>
                    <Metric label="total call GEX" value={fmtGexDollars(p.volumeTotals?.total_call_gex_vol ?? 0)} tone="pos" />
                    <Metric label="total put GEX" value={fmtGexDollars(-(p.volumeTotals?.total_put_gex_vol ?? 0))} tone="neg" />
                    <Metric label="net GEX" value={fmtGexDollars(p.volumeTotals?.net_gex_vol ?? 0)} colored={p.volumeTotals?.net_gex_vol} flashValue={p.volumeTotals?.net_gex_vol} emphasis />
                    <Metric label="call wall" value={fmtWall(p.volumeTotals?.call_wall_vol, p.wallAlternates?.vol.call)} tone="pos" />
                    <Metric label="put wall" value={fmtWall(p.volumeTotals?.put_wall_vol, p.wallAlternates?.vol.put)} tone="neg" />
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
          <RecordedToday symbol={symbol} data={data} />
        </div>
      ),
    },
    {
      id: "skew",
      label: "iv skew",
      render: () => (
        <div className="cards cards-wide">
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
                  { label: "call OI", color: "var(--pos)", value: (r) => r.call_oi },
                  { label: "put OI", color: "var(--neg)", value: (r) => -r.put_oi },
                  { label: "call vol", color: "color-mix(in srgb, var(--pos) 55%, var(--surface))", value: (r) => r.call_vol },
                  { label: "put vol", color: "color-mix(in srgb, var(--neg) 55%, var(--surface))", value: (r) => -r.put_vol },
                ]}
              />
            ) : (
              <span className="skeleton skeleton-text" style={{ width: "40%" }} />
            )}
          </section>

          <section className="card view-fade">
            <h2>Volume by strike (calls vs puts)</h2>
            {p?.ok && p.series && p.spot !== undefined ? (
              <StrikeBarsChart
                series={p.series}
                spot={p.spot}
                bars={[
                  { label: "call vol", color: "var(--pos)", value: (r) => r.call_vol },
                  { label: "put vol", color: "var(--neg)", value: (r) => -r.put_vol },
                ]}
              />
            ) : (
              <span className="skeleton skeleton-text" style={{ width: "40%" }} />
            )}
          </section>
        </div>
      ),
    },
  ];

  return (
    <ModuleFrame
      module="gex"
      slide={slide}
      slides={slides}
      session={null}
      headerControls={
        <>
          {symbols.data && (
            <select className="chip review-session-select" value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="symbol">
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
          {p?.ok && p.sessionMode === "last_session" && (
            <span
              className="chip chip-warn"
              title="outside regular hours: the last session's chain, not a live reading"
            >
              last session{p.chainAsOf != null ? ` · as of ${formatAsOf(p.chainAsOf)} ET` : ""}
            </span>
          )}
          {p?.ok && p.spotUpdatedAt != null && (
            <span
              className={asOfIsToday(p.spotUpdatedAt) ? "chip muted" : "chip chip-warn"}
              title="when the underlying's cached spot last updated -- the age of everything on this lightbox"
            >
              as of {formatAsOf(p.spotUpdatedAt)} ET
            </span>
          )}
          {activeSlide === "gex" && (
            <div className="mode-toggle" style={{ marginLeft: 0 }} role="group" aria-label="chart view">
              {(["oivol", "net", "abs"] as GexView[]).map((v) => (
                <button key={v} type="button" className={view === v ? "mode-btn active" : "mode-btn"} onClick={() => setView(v)}>
                  {v === "oivol" ? "OI vs Vol" : v === "net" ? "Net" : "Abs"}
                </button>
              ))}
            </div>
          )}
        </>
      }
      integrity={<IntegrityStrip data={integrity.data} updatedAt={integrity.dataUpdatedAt} />}
      integrityAttention={gexHasAttention(integrity.data)}
    />
  );
}
