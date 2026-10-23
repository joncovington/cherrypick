import { createContext, useContext } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ManageAccount, ManageLiveAccount, ManageLivePayload, ManageLiveTrade } from "@console/shared";
import { Card } from "../../components/DataTable";
import { fmtCash, fmtMoney } from "../../lib/format";
import { ageText, tone } from "./manageParts";

/**
 * Live marks (2026-10-22): the newest evaluation re-priced from the stream cache every few seconds
 * (`/api/manage/live`). Display only -- the verdicts, levels and the record are the evaluation's, and
 * nothing here re-judges a trade. A trade or a book figure with any stale input reads a dash.
 */

export function useManageLive(enabled: boolean) {
  return useQuery<ManageLivePayload>({
    queryKey: ["manage-live"],
    queryFn: async () => {
      const res = await fetch("/api/manage/live");
      if (!res.ok) throw new Error(`manage live: HTTP ${res.status}`);
      return (await res.json()) as ManageLivePayload;
    },
    enabled,
    // The streamer writes quotes as they tick; five seconds is live to the eye and cheap to read.
    refetchInterval: 5_000,
  });
}

export interface LiveMarks {
  payload: ManageLivePayload;
  trades: Map<string, ManageLiveTrade>;
}

/** Null while the page shows a past session, or before the first read. */
export const LiveMarksContext = createContext<LiveMarks | null>(null);

export function useLiveMarks(): LiveMarks | null {
  return useContext(LiveMarksContext);
}

export function liveMarks(payload: ManageLivePayload | undefined): LiveMarks | null {
  return payload ? { payload, trades: new Map(payload.trades.map((t) => [t.tradeId, t])) } : null;
}

function pct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(0)}%`;
}

/** Why a trade has no live figure, for its hover. */
function missing(t: ManageLiveTrade | undefined): string {
  if (!t) return "not in the newest evaluation";
  return `${t.legsQuoted} of ${t.legsTotal} legs have a fresh quote: no live mark until every leg does`;
}

export function LivePnlCell({ id }: { id: string }) {
  const live = useLiveMarks();
  const t = live?.trades.get(id);
  if (!t || t.pnlGross === null) {
    return (
      <td className="muted" style={{ textAlign: "right" }} title={live ? missing(t) : "live marks show on the newest evaluation"}>
        —
      </td>
    );
  }
  const shown = t.pnlNet ?? t.pnlGross;
  return (
    <td
      className={tone(shown)}
      style={{ textAlign: "right" }}
      title={`${t.pnlNet === null ? "gross (no fee record)" : `gross ${fmtMoney(t.pnlGross)}, net of the evaluation's fees`} · live value ${fmtCash(t.value)}`}
    >
      {fmtMoney(shown)}
      {t.pnlNet === null ? <span className="muted"> gr</span> : null}
    </td>
  );
}

export function LiveCaptureCell({ id }: { id: string }) {
  const live = useLiveMarks();
  const t = live?.trades.get(id);
  if (!t || t.capture === null) {
    return (
      <td className="muted" style={{ textAlign: "right" }} title={live ? missing(t) : undefined}>
        —
      </td>
    );
  }
  return (
    <td className={tone(t.capture)} style={{ textAlign: "right" }}>
      {pct(t.capture)}
      {t.atTakeProfit ? (
        <>
          {" "}
          <span className="chip chip-ok" title="the live value is at or past the evaluation's take-profit level: the next evaluation judges it">
            at TP
          </span>
        </>
      ) : null}
      {t.atStop ? (
        <>
          {" "}
          <span className="chip chip-bad" title="the live value is at or past the evaluation's stop level: the next evaluation judges it">
            at stop
          </span>
        </>
      ) : null}
    </td>
  );
}

/** One line under the page head: how live the live columns are. */
export function LiveStatus() {
  const live = useLiveMarks();
  if (!live) return null;
  const p = live.payload;
  const whole = p.trades.filter((t) => t.pnlGross !== null).length;
  if (whole === 0) {
    return (
      <p className="muted" style={{ margin: 0 }}>
        live marks: none streaming (no quote under {p.maxAgeSeconds} s old: the market is shut, the streamer is down, or the
        held legs are subscribed only from the next evaluation)
      </p>
    );
  }
  return (
    <p className="muted" style={{ margin: 0 }}>
      live marks: {whole} of {p.trades.length} trades from the stream
      {p.oldestQuoteAt !== null ? `, oldest quote ${ageText(Date.now() - p.oldestQuoteAt)} old` : ""} · on the evaluation's basis
      and levels; the verdicts are the evaluation's
    </p>
  );
}

/** Live → portfolio: the book's delta and theta from the streamed greeks, beside the evaluation's. */
export function LiveGreeksCard({ a }: { a: ManageAccount }) {
  const live = useLiveMarks();
  if (!live || !a.portfolio) return null;
  const l: ManageLiveAccount | undefined = live.payload.accounts.find((x) => x.account === a.account);
  if (!l) return null;
  const p = a.portfolio;
  const bands = p.bands;
  const netLiq = p.netLiq;
  // The theta band is a share of net liq; the live figure is read against the evaluation's net liq.
  const thetaShare = l.theta !== null && netLiq ? l.theta / netLiq : null;
  return (
    <Card title="live greeks" collapseKey="manage-live-greeks">
      <p className="muted pf-caption" style={{ marginTop: 0 }}>
        from the streamed greeks, beta-weighted with the evaluation's betas to {p.benchmark ?? "the benchmark"} · {l.legsWithGreeks} of{" "}
        {l.legsTotal} legs streaming{l.legsWithGreeks < l.legsTotal ? ": a dash until every leg is" : ""} · the streamer's greeks, so
        they differ a little from the evaluation's own model
      </p>
      <table className="data-table">
        <thead>
          <tr>
            <th />
            <th style={{ textAlign: "right" }}>live</th>
            <th style={{ textAlign: "right" }}>evaluation</th>
            <th title="the evaluation's band, for reading the live figure against; in or out is the evaluation's call, made at its next run">band</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>delta ({p.benchmark ?? "benchmark"} shares)</td>
            <td style={{ textAlign: "right" }}>{l.bwd === null ? "—" : l.bwd.toFixed(1)}</td>
            <td style={{ textAlign: "right" }}>{p.bwd === null ? "—" : p.bwd.toFixed(1)}</td>
            <td>
              {bands ? `${bands.delta.low?.toFixed(1) ?? "—"} to ${bands.delta.high?.toFixed(1) ?? "—"} ` : "—"}
            </td>
          </tr>
          <tr>
            <td>theta ($ a day)</td>
            <td style={{ textAlign: "right" }} title={thetaShare === null ? undefined : `${(thetaShare * 100).toFixed(2)}% of net liq`}>
              {fmtCash(l.theta)}
            </td>
            <td style={{ textAlign: "right" }}>{fmtCash(p.theta)}</td>
            <td>
              {bands ? `${pct2(bands.theta.low)} to ${pct2(bands.theta.high)} of net liq ` : "—"}
            </td>
          </tr>
        </tbody>
      </table>
    </Card>
  );
}

function pct2(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(2)}%`;
}
