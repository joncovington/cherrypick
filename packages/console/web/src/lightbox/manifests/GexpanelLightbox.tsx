import { useMemo, useState } from "react";
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

/** What the walls' gamma is weighted by (server `analytics/gexPanel.ts` `WallBasis`). */
type Basis = "oi" | "volume";

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
  sessionMode?: "live" | "last_session" | "next_session";
  chainAsOf?: number | null;
  secondsToExpiry?: number;
  lateSession?: boolean;
  netGex?: number;
  netGexVolume?: number;
  grossGex?: number;
  zeroGamma?: number | null;
  putCall?: { oi: number | null; volume: number | null };
  walls?: Record<Basis, { call: PanelWall[]; put: PanelWall[] }>;
  maxPain?: number | null;
  maxPainAtEdge?: boolean;
  oiCoverage?: { low: number; high: number; strikes: number } | null;
  atmIv?: number | null;
  straddle?: { mid: number; strikes: number[]; asOf: number } | null;
  moves?: { straddle: Band | null; iv: Band | null; ruleOfThumb: (Band & { factor: number }) | null };
  magnet?: { strike: number; share: number; oi: number; gammaSign: 1 | -1 | 0; basis: "straddle" | "iv" } | null;
  flow?: FlowLayer;
  spotHistory?: Array<{ ts: number; spot: number }>;
}

/** The session's prints, signed (server `analytics/gexFlow.ts`, mirroring `core.gex`). */
type FlowLayer =
  | { status: "absent" | "empty" | "failed"; reason: string }
  | {
      status: "ok";
      contracts: {
        counted: number;
        excluded: number;
        classifiedBy: { aggressor: number; quote: number; unclassified: number };
        excludedBy: { not_traded: number; invalid: number; extended_hours: number; spread_leg: number };
      };
      netBought: { call: number; put: number };
      netGexOi: number;
      netGexFlow: number;
      flowGex: number;
      zeroGammaFlow: number | null;
      topStrikes: Array<{ strike: number; net_gex: number; flow_gex: number; net_gex_flow: number }>;
    };

const fmtSigned = (v: number) => `${v > 0 ? "+" : ""}${fmtInt(v)}`;

function FlowSection({ flow }: { flow: FlowLayer | undefined }) {
  if (flow === undefined) return null;
  if (flow.status !== "ok") {
    return (
      <>
        <h3 className="gexp-section">flow (today&apos;s prints)</h3>
        <p className={`gexp-empty ${flow.status === "failed" ? "pnl-neg" : "muted"}`}>{flow.reason}</p>
      </>
    );
  }
  const c = flow.contracts;
  const classified = c.classifiedBy.aggressor + c.classifiedBy.quote;
  const pctOf = (n: number, of: number) => (of > 0 ? fmtPct(n / of) : "—");
  return (
    <>
      <h3 className="gexp-section" title="each print signed by who initiated it: the feed's aggressor side, else the quote at the print">
        flow (today&apos;s prints)
      </h3>
      <div className="gexp-grid">
        <Row label="net GEX, OI + flow" value={fmtGexDollars(flow.netGexFlow)} tone={flow.netGexFlow >= 0 ? "pnl-pos" : "pnl-neg"} />
        <Row label="flow's GEX" value={fmtGexDollars(flow.flowGex)} tone={flow.flowGex >= 0 ? "pnl-pos" : "pnl-neg"} />
        <Row label="calls bought net" value={fmtSigned(flow.netBought.call)} />
        <Row label="puts bought net" value={fmtSigned(flow.netBought.put)} />
        <Row
          label="zero gamma, + flow"
          value={fmtPx(flow.zeroGammaFlow)}
          title="nearest strike where the flow-adjusted per-strike net changes sign"
        />
        <Row
          label="signed"
          value={`${pctOf(classified, c.counted)} of ${fmtInt(c.counted)}`}
          title={`contracts signed: ${fmtInt(c.classifiedBy.aggressor)} by the feed's aggressor side, ${fmtInt(c.classifiedBy.quote)} by the quote at the print; ${fmtInt(c.classifiedBy.unclassified)} traded between bid and ask and are left unsigned`}
        />
      </div>
      {flow.topStrikes.length > 0 && (
        <table className="gexp-walls">
          <tbody>
            {flow.topStrikes.map((s) => (
              <tr key={s.strike}>
                <td className="gexp-strike">{s.strike.toFixed(2)}</td>
                <td className="muted">{fmtGexDollars(s.net_gex)}</td>
                <td className="muted">→</td>
                <td className={s.net_gex_flow >= 0 ? "pnl-pos" : "pnl-neg"}>{fmtGexDollars(s.net_gex_flow)}</td>
                <td className={s.flow_gex >= 0 ? "pnl-pos" : "pnl-neg"} title="this strike's flow GEX">
                  ({fmtGexDollars(s.flow_gex)})
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted gexp-foot">
        Excluded {fmtInt(c.excluded)} contracts: {fmtInt(c.excludedBy.spread_leg)} spread legs (a multi-leg order prints each
        leg), {fmtInt(c.excludedBy.extended_hours)} extended hours, {fmtInt(c.excludedBy.not_traded + c.excludedBy.invalid)}{" "}
        cancelled or invalid. A customer&apos;s net buy of either right is taken to leave the dealer that much shorter; an
        initiating buyer may be closing, not opening.
      </p>
    </>
  );
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
function levelsOf(p: GexPanelPayload, basis: Basis): Level[] {
  const out: Level[] = [];
  p.walls?.[basis].call.forEach((w, i) => out.push({ price: w.strike, title: `CW${i + 1}`, token: "--pos", style: i === 0 ? "solid" : "dashed" }));
  p.walls?.[basis].put.forEach((w, i) => out.push({ price: w.strike, title: `PW${i + 1}`, token: "--neg", style: i === 0 ? "solid" : "dashed" }));
  if (p.zeroGamma != null) out.push({ price: p.zeroGamma, title: "ZG", token: "--chart-special", style: "solid" });
  if (p.flow?.status === "ok" && p.flow.zeroGammaFlow != null) {
    out.push({ price: p.flow.zeroGammaFlow, title: "ZG+flow", token: "--chart-special", style: "dashed" });
  }
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

const BASIS_TITLE: Record<Basis, string> = {
  oi: "gamma x open interest: the broker's once-a-day count, positioning as of the open",
  volume: "gamma x the session's volume: what has traded since the open, which a 0DTE chain does many times over",
};

function Panel({ p, basis, setBasis }: { p: GexPanelPayload; basis: Basis; setBasis: (b: Basis) => void }) {
  const spot = p.spot!;
  const aboveFlip = p.zeroGamma != null ? spot > p.zeroGamma : null;
  const mag = p.magnet;
  const walls = p.walls?.[basis];
  const cov = p.oiCoverage;
  const by = basis === "oi" ? "OI" : "volume";
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
        <Row label="net GEX, OI" value={fmtGexDollars(p.netGex ?? 0)} tone={(p.netGex ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"} />
        <Row
          label="net GEX, volume"
          value={fmtGexDollars(p.netGexVolume ?? 0)}
          tone={(p.netGexVolume ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}
          title={BASIS_TITLE.volume}
        />
        <Row label="gross, OI" value={fmtGexDollars(p.grossGex ?? 0)} />
        <Row
          label="P/C OI"
          value={fmtPx(p.putCall?.oi)}
          title={cov != null ? `over the ${cov.strikes} strikes with open interest in the stream, ${cov.low}–${cov.high}` : undefined}
        />
        <Row label="P/C vol" value={fmtPx(p.putCall?.volume)} />
      </div>

      <div className="mode-toggle gexp-basis" role="group" aria-label="wall basis">
        {(["oi", "volume"] as const).map((b) => (
          <button key={b} type="button" className={basis === b ? "mode-btn active" : "mode-btn"} aria-pressed={basis === b} title={BASIS_TITLE[b]} onClick={() => setBasis(b)}>
            walls by {b === "oi" ? "OI" : "volume"}
          </button>
        ))}
      </div>
      <h3 className="gexp-section pnl-pos" title={`ranked by call gamma (call gamma x call ${by}), SpotGamma's call wall`}>call walls (above spot)</h3>
      <WallRows walls={walls?.call ?? []} side="call" />
      <h3 className="gexp-section pnl-neg" title={`ranked by put gamma (put gamma x put ${by}), SpotGamma's put wall`}>put walls (below spot)</h3>
      <WallRows walls={walls?.put ?? []} side="put" />

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
        <Row
          label="max pain"
          value={p.maxPain != null && p.maxPainAtEdge === true ? `${fmtPx(p.maxPain)} (edge)` : fmtPx(p.maxPain)}
          tone={p.maxPainAtEdge === true ? "gexp-amber" : undefined}
          title={
            `the strike that pays the open interest least at expiry, over the strikes the stream covers` +
            (cov != null ? ` (${cov.strikes}, ${cov.low}–${cov.high})` : "") +
            (p.maxPainAtEdge === true
              ? ": it sits at the edge of that coverage, where open interest beyond it could move it, so do not trust it"
              : "; open interest beyond them could still shift it")
          }
        />
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
      <FlowSection flow={p.flow} />
      <p className="muted gexp-foot">
        Broker data only. Open interest as the broker reports it (once a day), with today&apos;s volume beside each wall; the moves
        follow each quote and the clock to expiry, and show nothing once the chain has expired. No depth feed here, so the copied
        panel&apos;s DOM rows are left out. Untested: display only.
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
  const [basis, setBasis] = useState<Basis>("oi");
  const levels = useMemo(() => (p?.ok ? levelsOf(p, basis) : []), [p, basis]);
  const sigma = p?.moves?.straddle ?? p?.moves?.iv ?? null;
  const fit = sigma !== null ? sigma.move * 1.5 : (p?.spot ?? 0) * 0.01;

  const slides: SlideDef[] = [
    {
      id: "panel",
      label: "panel",
      render: () =>
        p?.ok && p.spot !== undefined ? (
          <div className="gexp-layout">
            <Panel p={p} basis={basis} setBasis={setBasis} />
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
          {p?.ok && p.sessionMode === "next_session" && (
            <span
              className="chip chip-warn"
              title="outside regular hours: the next session's chain, priced off the last session's close; the trail is that last session's"
            >
              next session · {p.expiration}
              {p.chainAsOf != null ? ` · greeks as of ${formatAsOf(p.chainAsOf)} ET` : ""}
            </span>
          )}
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
