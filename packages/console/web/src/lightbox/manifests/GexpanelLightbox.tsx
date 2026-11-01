import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fmtGexDollars } from "../../pages/Gex/GexProfileChart";
import { LevelsChart, type Level } from "../../pages/GexPanel/LevelsChart";
import { asOfIsToday, formatAsOf } from "./GexLightbox";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

interface PanelWall {
  strike: number;
  netGex: number;
  share: number;
  oi: number;
  volume: number;
  volOi: number | null;
}

interface GexPanelPayload {
  ok: boolean;
  error?: string;
  spot?: number;
  spotUpdatedAt?: number | null;
  expiration?: string;
  sessionMode?: "live" | "last_session";
  chainAsOf?: number | null;
  netGex?: number;
  grossGex?: number;
  zeroGamma?: number | null;
  putCall?: { oi: number | null; volume: number | null };
  walls?: { call: PanelWall[]; put: PanelWall[] };
  maxPain?: number | null;
  atmIv?: { strike: number; iv: number } | null;
  expectedMove?: { move: number; low: number; high: number } | null;
  straddle?: { strike: number; callMid: number; putMid: number; mid: number; asOf: number; band: number; low: number; high: number } | null;
  magnet?: { strike: number; share: number; basis: "straddle" | "expected_move" } | null;
  spotHistory?: Array<{ ts: number; spot: number }>;
}

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
            <td className={tone}>{fmtGexDollars(w.netGex)}</td>
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

/** The chart's levels: walls, zero gamma, magnet, max pain, the straddle band and the 1-sigma move. */
function levelsOf(p: GexPanelPayload): Level[] {
  const out: Level[] = [];
  p.walls?.call.forEach((w, i) => out.push({ price: w.strike, title: `CW${i + 1}`, token: "--pos", style: i === 0 ? "solid" : "dashed" }));
  p.walls?.put.forEach((w, i) => out.push({ price: w.strike, title: `PW${i + 1}`, token: "--neg", style: i === 0 ? "solid" : "dashed" }));
  if (p.zeroGamma != null) out.push({ price: p.zeroGamma, title: "ZG", token: "--chart-special", style: "solid" });
  if (p.magnet != null) out.push({ price: p.magnet.strike, title: "MAG", token: "--amber-line", style: "solid" });
  if (p.maxPain != null) out.push({ price: p.maxPain, title: "MP", token: "--text-muted", style: "dotted" });
  if (p.straddle != null) {
    out.push({ price: p.straddle.high, title: `+STDL ${p.straddle.band}x`, token: "--accent", style: "dotted" });
    out.push({ price: p.straddle.low, title: `-STDL ${p.straddle.band}x`, token: "--accent", style: "dotted" });
  }
  if (p.expectedMove != null) {
    out.push({ price: p.expectedMove.high, title: "+EM", token: "--chart-special-2", style: "dotted" });
    out.push({ price: p.expectedMove.low, title: "-EM", token: "--chart-special-2", style: "dotted" });
  }
  return out;
}

function Panel({ p }: { p: GexPanelPayload }) {
  const spot = p.spot!;
  const aboveFlip = p.zeroGamma != null ? spot > p.zeroGamma : null;
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
      </div>
      <div className="gexp-grid">
        <Row label="net GEX" value={fmtGexDollars(p.netGex ?? 0)} tone={(p.netGex ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"} />
        <Row label="gross" value={fmtGexDollars(p.grossGex ?? 0)} />
        <Row label="P/C OI" value={fmtPx(p.putCall?.oi)} />
        <Row label="P/C vol" value={fmtPx(p.putCall?.volume)} />
      </div>

      <h3 className="gexp-section pnl-pos">call walls (above spot)</h3>
      <WallRows walls={p.walls?.call ?? []} side="call" />
      <h3 className="gexp-section pnl-neg">put walls (below spot)</h3>
      <WallRows walls={p.walls?.put ?? []} side="put" />

      <div className="gexp-grid gexp-levels">
        <Row
          label="zero gamma"
          value={p.zeroGamma != null ? `${p.zeroGamma.toFixed(2)} (${(spot - p.zeroGamma >= 0 ? "+" : "") + (spot - p.zeroGamma).toFixed(2)})` : "—"}
          title="nearest strike where per-strike net GEX changes sign, as on the GEX page"
        />
        <Row
          label="magnet"
          value={p.magnet != null ? `${p.magnet.strike.toFixed(2)} (${fmtPct(p.magnet.share)})` : "—"}
          tone="gexp-amber"
          title={`largest |net GEX| inside the ${p.magnet?.basis === "expected_move" ? "1-sigma move" : "straddle band"}, and its share of that band's |net GEX|: our definition, not the copied panel's`}
        />
        <Row label="max pain" value={fmtPx(p.maxPain)} title="the strike that pays the chain's open interest least at expiry" />
        <Row label="ATM IV" value={p.atmIv != null ? fmtPct(p.atmIv.iv) : "—"} title={p.atmIv != null ? `mean of call and put IV at ${p.atmIv.strike}` : undefined} />
        <Row
          label="EM (1σ to expiry)"
          value={p.expectedMove != null ? `${p.expectedMove.low.toFixed(2)} – ${p.expectedMove.high.toFixed(2)}` : "—"}
          title="spot x ATM IV x sqrt(time left to the 16:00 expiry); none once it has passed"
        />
        <Row
          label={`stdl ${p.straddle?.band ?? 0.85}x`}
          value={p.straddle != null ? `${p.straddle.low.toFixed(2)} – ${p.straddle.high.toFixed(2)}` : "—"}
          title={p.straddle != null ? `ATM ${p.straddle.strike} straddle ${p.straddle.mid.toFixed(2)} at mid, as of ${formatAsOf(p.straddle.asOf)} ET` : "no ATM quote on both sides"}
        />
      </div>
      <p className="muted gexp-foot">
        Levels from open interest as the broker reports it (once a day), with today&apos;s volume beside each wall. No tape or
        depth feed here, so the copied panel&apos;s order-flow rows are left out. Untested: display only.
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
  const fit = p?.expectedMove != null ? p.expectedMove.move * 1.5 : (p?.spot ?? 0) * 0.01;

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
