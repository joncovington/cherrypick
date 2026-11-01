import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fmtGexDollars } from "../../pages/Gex/GexProfileChart";
import { LevelsChart, type Level } from "../../pages/GexPanel/LevelsChart";
import { asOfIsToday, formatAsOf } from "./GexLightbox";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

interface PanelWall {
  strike: number;
  /** The side's GEX: call positive, put negative. */
  gex: number;
  share: number;
  oi: number;
  volume: number;
  volOi: number | null;
}

interface Band {
  move: number;
  low: number;
  high: number;
}

interface GexPanelPayload {
  ok: boolean;
  error?: string;
  spot?: number;
  spotUpdatedAt?: number | null;
  expiration?: string;
  sessionMode?: "live" | "last_session";
  chainAsOf?: number | null;
  secondsToExpiry?: number;
  lateSession?: boolean;
  netGex?: number;
  grossGex?: number;
  zeroGamma?: number | null;
  putCall?: { oi: number | null; volume: number | null };
  walls?: { call: PanelWall[]; put: PanelWall[] };
  maxPain?: number | null;
  atmIv?: number | null;
  straddle?: { mid: number; strikes: number[]; asOf: number } | null;
  moves?: { straddle: Band | null; iv: Band | null; ruleOfThumb: (Band & { factor: number }) | null };
  magnet?: { strike: number; share: number; oi: number; gammaSign: 1 | -1 | 0; basis: "straddle" | "iv" } | null;
  spotHistory?: Array<{ ts: number; spot: number }>;
}

/** Every 15 s, the console's quote cadence: the moves follow each quote and the clock to expiry. */
function useGexPanel() {
  return useQuery<GexPanelPayload>({
    queryKey: ["gex-panel"],
    queryFn: async () => {
      const res = await fetch("/api/gex/panel");
      if (!res.ok) throw new Error(`gex panel: HTTP ${res.status}`);
      return (await res.json()) as GexPanelPayload;
    },
    refetchInterval: 15_000,
  });
}

const fmtPct = (v: number) => `${(v * 100).toFixed(1)}%`;
const fmtPx = (v: number | null | undefined, dp = 2) => (v === null || v === undefined ? "—" : v.toFixed(dp));
const fmtInt = (v: number) => v.toLocaleString("en-US");
const fmtRatio = (v: number | null) => (v === null ? "—" : v >= 10 ? ">10x" : `${v.toFixed(1)}x`);
const fmtBand = (b: Band | null | undefined) => (b == null ? "—" : `±${b.move.toFixed(2)}  ${b.low.toFixed(2)} – ${b.high.toFixed(2)}`);

function WallRows({ walls, side }: { walls: PanelWall[]; side: "call" | "put" }) {
  if (walls.length === 0) return <p className="muted gexp-empty">no {side} wall {side === "call" ? "above" : "below"} spot</p>;
  const tone = side === "call" ? "pnl-pos" : "pnl-neg";
  return (
    <table className="gexp-walls">
      <tbody>
        {walls.map((w, i) => (
          <tr key={w.strike}>
            <td className={`gexp-tag ${tone}`}>{side === "call" ? "CW" : "PW"}{i + 1}</td>
            <td className="gexp-strike">{w.strike.toFixed(2)}</td>
            <td className="gexp-bar">
              <span className={tone} style={{ width: `${Math.min(100, w.share * 100 * 3)}%` }} />
            </td>
            <td className={tone}>{fmtPct(w.share)}</td>
            <td className="muted">{fmtInt(w.oi)} OI</td>
            <td className={tone}>{fmtGexDollars(w.gex)}</td>
            <td className="gexp-volchip" title="the side's volume today over its open interest">vol {fmtRatio(w.volOi)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Row({ label, value, tone, title }: { label: string; value: string; tone?: string; title?: string }) {
  return (
    <div className="gexp-row" title={title}>
      <span className="muted">{label}</span>
      <span className={tone}>{value}</span>
    </div>
  );
}

/** The chart's levels: walls, zero gamma, magnet, max pain, both 1-sigma moves and the 0.85 band. */
function levelsOf(p: GexPanelPayload): Level[] {
  const out: Level[] = [];
  p.walls?.call.forEach((w, i) => out.push({ price: w.strike, title: `CW${i + 1}`, token: "--pos", style: i === 0 ? "solid" : "dashed" }));
  p.walls?.put.forEach((w, i) => out.push({ price: w.strike, title: `PW${i + 1}`, token: "--neg", style: i === 0 ? "solid" : "dashed" }));
  if (p.zeroGamma != null) out.push({ price: p.zeroGamma, title: "ZG", token: "--chart-special", style: "solid" });
  if (p.magnet != null) out.push({ price: p.magnet.strike, title: "MAG", token: "--amber-line", style: "solid" });
  if (p.maxPain != null) out.push({ price: p.maxPain, title: "MP", token: "--text-muted", style: "dotted" });
  const pair = (b: Band | null | undefined, title: string, token: string, style: Level["style"]) => {
    if (b == null) return;
    out.push({ price: b.high, title: `+${title}`, token, style });
    out.push({ price: b.low, title: `-${title}`, token, style });
  };
  pair(p.moves?.straddle, "1σ stdl", "--accent", "dashed");
  pair(p.moves?.iv, "1σ IV", "--chart-special-2", "dotted");
  pair(p.moves?.ruleOfThumb, `${p.moves?.ruleOfThumb?.factor ?? 0.85}x`, "--text-dim", "dotted");
  return out;
}

const SIGN_LABEL = { 1: "+γ", [-1]: "−γ", 0: "0γ" } as const;

function Panel({ p }: { p: GexPanelPayload }) {
  const spot = p.spot!;
  const aboveFlip = p.zeroGamma != null ? spot > p.zeroGamma : null;
  const mag = p.magnet;
  return (
    <section className="card view-fade gexp-card">
      <div className="gexp-head">
        <span className="gexp-sym">SPX</span>
        <span className="gexp-spot">{spot.toFixed(2)}</span>
        <span className="chip">exp {p.expiration}</span>
        {aboveFlip !== null && (
          <span className={`chip ${aboveFlip ? "pnl-pos" : "pnl-neg"}`} title="spot against the nearest zero-gamma strike">
            {aboveFlip ? "above flip" : "below flip"}
          </span>
        )}
        {p.lateSession === true && (
          <span className="chip chip-warn" title="the last 30 minutes: 0DTE quotes thin and widen, so the moves are mostly spread">
            late session
          </span>
        )}
      </div>
      <div className="gexp-grid">
        <Row label="net GEX" value={fmtGexDollars(p.netGex ?? 0)} tone={(p.netGex ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"} />
        <Row label="gross" value={fmtGexDollars(p.grossGex ?? 0)} />
        <Row label="P/C OI" value={fmtPx(p.putCall?.oi)} />
        <Row label="P/C vol" value={fmtPx(p.putCall?.volume)} />
      </div>

      <h3 className="gexp-section pnl-pos" title="ranked by call gamma (call gamma x call OI), SpotGamma's call wall">call walls (above spot)</h3>
      <WallRows walls={p.walls?.call ?? []} side="call" />
      <h3 className="gexp-section pnl-neg" title="ranked by put gamma (put gamma x put OI), SpotGamma's put wall">put walls (below spot)</h3>
      <WallRows walls={p.walls?.put ?? []} side="put" />

      <div className="gexp-grid gexp-levels">
        <Row
          label="zero gamma"
          value={p.zeroGamma != null ? `${p.zeroGamma.toFixed(2)} (${(spot - p.zeroGamma >= 0 ? "+" : "") + (spot - p.zeroGamma).toFixed(2)})` : "—"}
          title="nearest strike where per-strike net GEX changes sign, as on the GEX page"
        />
        <Row
          label="magnet"
          value={mag != null ? `${mag.strike.toFixed(2)} (${fmtPct(mag.share)}, ${SIGN_LABEL[mag.gammaSign]})` : "—"}
          tone="gexp-amber"
          title={`most open interest inside the ${mag?.basis === "iv" ? "IV" : "straddle"} 1-sigma band, its share of the band's OI, and the sign of its net gamma (positive is said to pin, negative to push away): our definition, there is no standard one`}
        />
        <Row label="max pain" value={fmtPx(p.maxPain)} title="the strike that pays the chain's open interest least at expiry" />
        <Row label="ATM IV" value={p.atmIv != null ? fmtPct(p.atmIv) : "—"} title="mean of call and put IV, interpolated to spot between the strikes either side" />
      </div>
      <div className="gexp-moves">
        <Row
          label="1σ straddle"
          value={fmtBand(p.moves?.straddle)}
          tone="gexp-accent"
          title={
            p.straddle != null
              ? `straddle ${p.straddle.mid.toFixed(2)} at spot (from ${p.straddle.strikes.join(" / ")}, as of ${formatAsOf(p.straddle.asOf)} ET) / 0.798: the market's 1-sigma move to expiry`
              : "no usable straddle quote around spot, or the chain has expired"
          }
        />
        <Row label="1σ IV" value={fmtBand(p.moves?.iv)} title="spot x ATM IV x sqrt(time left to the 16:00 expiry)" />
        <Row
          label={`${p.moves?.ruleOfThumb?.factor ?? 0.85}x stdl (≈0.68σ)`}
          value={fmtBand(p.moves?.ruleOfThumb)}
          title="the copied panel's band: 0.85 x the straddle, a rule of thumb that is about 0.68 sigma, not 1"
        />
      </div>
      <p className="muted gexp-foot">
        Broker data only. Open interest as the broker reports it (once a day), with today&apos;s volume beside each wall; the moves
        follow each quote and the clock to expiry, and show nothing once the chain has expired. No tape or depth feed here, so the
        copied panel&apos;s order-flow rows are left out. Untested: display only.
      </p>
    </section>
  );
}

/**
 * The experimental GEX panel (2026-10-31): a copy of a third-party 0DTE walls panel over the GEX
 * page's own SPX profile, beside today's spot trail with the levels drawn on it. One page. The
 * levels' definitions live in the server's `analytics/gexPanel.ts`; nothing here derives one.
 */
export function GexpanelLightbox({ slide }: { slide: string }) {
  const q = useGexPanel();
  const p = q.data;
  const levels = useMemo(() => (p?.ok ? levelsOf(p) : []), [p]);
  const sigma = p?.moves?.straddle ?? p?.moves?.iv ?? null;
  const fit = sigma !== null ? sigma.move * 1.5 : (p?.spot ?? 0) * 0.01;

  const slides: SlideDef[] = [
    {
      id: "panel",
      label: "panel",
      render: () =>
        p?.ok && p.spot !== undefined ? (
          <div className="gexp-layout">
            <Panel p={p} />
            <section className="card view-fade gexp-chart">
              <h2>SPX today, with the panel&apos;s levels</h2>
              {(p.spotHistory?.length ?? 0) > 1 ? (
                <LevelsChart trail={p.spotHistory!} levels={levels} spot={p.spot} fitWithin={fit} />
              ) : (
                <p className="muted">No spot trail recorded for this session yet.</p>
              )}
            </section>
          </div>
        ) : q.isLoading ? (
          <span className="skeleton skeleton-text" style={{ width: "50%" }} />
        ) : (
          <p className="muted">{p?.error ?? (q.error as Error | null)?.message ?? "no panel"}</p>
        ),
    },
  ];

  return (
    <ModuleFrame
      module="gexpanel"
      slide={slide}
      slides={slides}
      session={null}
      headerControls={
        <>
          {p?.ok && p.sessionMode === "last_session" && (
            <span className="chip chip-warn" title="outside regular hours: the last session's chain, not a live reading">
              last session{p.chainAsOf != null ? ` · as of ${formatAsOf(p.chainAsOf)} ET` : ""}
            </span>
          )}
          {p?.ok && p.spotUpdatedAt != null && (
            <span className={asOfIsToday(p.spotUpdatedAt) ? "chip muted" : "chip chip-warn"}>as of {formatAsOf(p.spotUpdatedAt)} ET</span>
          )}
        </>
      }
    />
  );
}
