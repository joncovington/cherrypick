import { useQuery } from "@tanstack/react-query";
import type { LiveFliesPayload, LiveOnRisk, LivePerformance, LivePeriod, LivePosition } from "@console/shared";
import { Card, DataCard } from "../../components/DataTable";
import { EquityUnderwater } from "../../components/EquityUnderwater";
import { MetricTiles } from "../../components/performance/MetricTiles";
import { fmtMoney } from "../../lib/format";
import {
  CompletionCard,
  CompletionTrend,
  LiveVsPaperCard,
  MissesCard,
  Tile as PerfTile,
  fmtRatio,
  tone as perfTone,
} from "../Flies/PerformanceTab";
import { Pager, usePage } from "../../components/ScopeBar";
import { LiveChart } from "./LiveChart";

const ACTIVITY_PAGE_SIZES = [10, 25, 50, 100] as const;

/**
 * The flies live pilot's day, read-only (2026-09-17). Two honesty rules the page states on its
 * face: the period tiles are SETTLED net only (flies settles at expiry, so today reads zero until
 * the bell, and the intraday number beside it is the mark), and a mark is a MID, not a fill. There
 * is no button here that touches an order: the desk CLI is the suite's only discretionary path,
 * and this console holds no order code by design.
 */
export function useLiveFlies() {
  return useQuery<LiveFliesPayload>({
    queryKey: ["live-flies"],
    queryFn: async () => {
      const res = await fetch("/api/live/flies");
      if (!res.ok) throw new Error(`live: HTTP ${res.status}`);
      return (await res.json()) as LiveFliesPayload;
    },
    refetchInterval: 15_000,
  });
}

function signed(v: number | null): string {
  if (v === null) return "—";
  return fmtMoney(v);
}

function tone(v: number | null | undefined): string {
  if (v === null || v === undefined) return "muted";
  return v >= 0 ? "pnl-pos" : "pnl-neg";
}

/** A signed return, e.g. `+32.8%`; an em-dash where it is undefined, never 0. */
function fmtReturn(r: number | null): string {
  if (r === null) return "—";
  return `${r >= 0 ? "+" : ""}${(r * 100).toFixed(1)}%`;
}

function onRiskTitle(o: LiveOnRisk): string {
  return (
    `settled net ${fmtMoney(o.net)} over the sum of each session's peak worst-case exposure ${fmtMoney(o.peakRisk)}, ` +
    `${o.sessions} of ${o.of} session${o.of === 1 ? "" : "s"} (a session counts once it is finished and recorded a peak; ` +
    `before 2026-09-17 none did)`
  );
}

function PeriodTile({ label, p }: { label: string; p: LivePeriod }) {
  return (
    <div className="stat-tile" title={`settled net over ${p.sessions} session${p.sessions === 1 ? "" : "s"}, ${p.trades} structure${p.trades === 1 ? "" : "s"}; paper control the same period: ${p.paperNet === null ? "n/a" : fmtMoney(p.paperNet)}\n\n${onRiskTitle(p.onRisk)}`}>
      <span className="stat-label">{label} · settled</span>
      <span className={`stat-value ${tone(p.trades > 0 ? p.net : null)}`}>{p.trades > 0 ? fmtMoney(p.net) : "—"}</span>
      <span className="muted" style={{ display: "block", fontSize: 11 }}>
        {p.trades} live · paper {p.paperNet === null ? "—" : fmtMoney(p.paperNet)}
      </span>
      <span className="muted" style={{ display: "block", fontSize: 11 }}>
        <span className={tone(p.onRisk.ratio)}>{fmtReturn(p.onRisk.ratio)}</span> on session peak risk
        {p.onRisk.ratio !== null && p.onRisk.sessions < p.onRisk.of ? ` (${p.onRisk.sessions}/${p.onRisk.of})` : ""}
      </span>
    </div>
  );
}

/** The flies study tabs' live figures for the pilot's arm, plus return on session peak risk. */
function PerformanceSection({ perf }: { perf: LivePerformance }) {
  const t = perf.tiles;
  const r = perf.risk;
  return (
    <>
      <section className="card">
        <div className="panel-head-row">
          <h2>performance · {perf.arm ?? "every arm"} · every live session</h2>
          <span className="muted lbl">the flies Performance tab, live mode, this arm</span>
        </div>
        <div className="stats-grid">
          <PerfTile
            label="return on session peak risk"
            value={fmtReturn(perf.onRisk.ratio)}
            tone={perfTone(perf.onRisk.ratio)}
            title={onRiskTitle(perf.onRisk)}
          />
          <PerfTile label="net P&L" value={fmtMoney(t.netPnl)} tone={perfTone(t.netPnl)} />
          <PerfTile label="trades · sessions" value={`${t.trades} · ${t.sessions}`} />
          <PerfTile label="win rate" value={t.winRatePct !== null ? `${t.winRatePct.toFixed(0)}%` : "—"} />
          <PerfTile label="profit factor" value={fmtRatio(t.profitFactor)} />
          <PerfTile label="fee drag" value={t.feeDragPct !== null ? `${t.feeDragPct.toFixed(1)}%` : "—"} tone="dim" />
          <PerfTile label="completion" value={t.completionRatePct !== null ? `${t.completionRatePct.toFixed(0)}%` : "—"} />
          <PerfTile label="sharpe (daily, ann.)" value={fmtRatio(r.sharpe)} tone={perfTone(r.sharpe)} />
          <PerfTile label="sortino (daily, ann.)" value={fmtRatio(r.sortino)} tone={perfTone(r.sortino)} />
          <PerfTile label="calmar" value={fmtRatio(r.calmar)} tone={perfTone(r.calmar)} />
          <PerfTile
            label="max drawdown (daily)"
            value={perf.maxDrawdown !== null ? fmtMoney(perf.maxDrawdown) : "—"}
            tone="neg"
            title="peak-to-trough of the cumulative DAILY net; the calibration reading below measures it trade by trade, so its figure can be larger"
          />
        </div>
        <p className="muted lbl" style={{ marginTop: "0.5rem" }}>
          Ratios annualized on 252 sessions from {r.sampleSize} of them — a ratio over that few sessions describes this
          stretch, not the strategy.
          {r.sharpeOverfitFlag && " Sharpe above 3 on a sample this small is a warning about the sample."}
        </p>
      </section>

      <section className="card">
        <div className="panel-head-row">
          <h2>calibration reading · {perf.arm ?? "every arm"} · live ledger</h2>
          <span className="muted lbl">
            the flies performance slide's tiles (core.metrics), per trade, from {perf.calibration.from ?? "all time"}
          </span>
        </div>
        {perf.calibration.reading !== null ? (
          <MetricTiles reading={perf.calibration.reading} peakRisk={perf.onRisk} />
        ) : (
          <p className={perf.calibration.error !== null ? "pnl-neg" : "muted"}>
            {perf.calibration.error ?? "no closed live trades for this arm in the window"}
          </p>
        )}
        <p className="muted lbl" style={{ marginTop: "0.5rem" }}>
          Flies has no return on capital or capture rate: the ledger carries no per-trade capital or ceiling,
          because a legged book's risk depends on completion. Return on peak risk takes return on capital's
          place; capture rate reads —.
        </p>
      </section>

      <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(28rem, 1fr))" }}>
        <DataCard
          title="return on session peak risk · by session"
          headers={["session", "trades", "net P&L", "peak risk", "at", "net / peak risk"]}
          loading={false}
          isError={false}
          empty="no settled live sessions"
          rowCount={perf.sessions.length}
          numFrom={1}
        >
          {perf.sessions.map((s) => (
            <tr key={s.session}>
              <td>{s.session}</td>
              <td>{s.trades}</td>
              <td className={tone(s.trades > 0 ? s.net : null)}>{s.trades > 0 ? fmtMoney(s.net) : "—"}</td>
              <td title="the session's largest open worst-case exposure — the figure the buying-power cap reads">
                {s.peakRisk !== null ? fmtMoney(s.peakRisk) : "n/r"}
              </td>
              <td>{hhmmOf(s.peakAt)}</td>
              <td className={tone(s.onRisk)} title={s.complete ? "" : "still open — the settled net is not the day's result yet"}>
                {s.complete ? fmtReturn(s.onRisk) : "open"}
              </td>
            </tr>
          ))}
        </DataCard>
        <section className="card">
          <h2>Cumulative net P&amp;L and drawdown (1-lot samples, not a sized book)</h2>
          {perf.equity.length === 0 ? <p className="muted">not enough history yet</p> : <EquityUnderwater equity={perf.equity} />}
        </section>
      </div>

      <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(22rem, 1fr))" }}>
        <CompletionCard c={perf.completion} />
        <MissesCard c={perf.completion} />
        {perf.liveVsPaper !== null && <LiveVsPaperCard lvp={perf.liveVsPaper} />}
      </div>

      <section className="card">
        <h2>Completion rate by session</h2>
        <CompletionTrend trend={perf.completionTrend} />
      </section>
    </>
  );
}

function hhmmOf(ts: string | null): string {
  return ts !== null && ts.length >= 16 ? ts.slice(11, 16) : "—";
}

function structure(p: LivePosition): string {
  const w = p.wingWidth;
  if (p.kind === "fly") return `${p.side} fly ${p.center} ±${w}`;
  if (p.kind === "short_vertical") return `short ${p.side} ${p.center}/${p.side === "put" ? p.center - w : p.center + w}`;
  return `${p.kind} ${p.side} ${p.center}`;
}

function fillState(p: LivePosition): string {
  if (p.status === "settled") return "settled";
  if (p.status === "cancelled") return `cancelled (${p.entryFillStatus ?? "?"})`;
  if (p.entryFillStatus === "pending") return "entry working";
  if (p.completionFillStatus === "pending") return `completion resting${p.mark?.restingLimit != null ? ` @ ${p.mark.restingLimit.toFixed(2)}` : ""}`;
  if (p.kind === "fly") return "complete";
  return "open spread";
}

export function LivePage() {
  const { data, isLoading, isError, dataUpdatedAt } = useLiveFlies();
  const d = data;
  const bp = d?.buyingPower;
  const acct = bp?.account ?? null;
  const capPct = bp && bp.cap ? Math.min(100, (bp.open / bp.cap) * 100) : null;
  const heldPeak = d !== undefined && d.today.open === 0 && d.today.pending === 0 ? d.today.sessionPeakWorst : null;
  // Before today's open the server hands back the last session the pilot settled; every "today"
  // on the page is that session, so the tile says which day it is.
  const lastCompleted = d?.sessionBasis === "last_completed";
  // The feed is the whole day's journal, already in hand, so it pages client-side. Back to the
  // first page when the session changes -- an offset into yesterday's feed points nowhere today.
  const activity = usePage([d?.session], ACTIVITY_PAGE_SIZES[0]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.8rem" }}>
      {d && d.ledger !== "ok" && (
        <p className={d.ledger === "failed" ? "pnl-neg" : "muted"}>
          {d.ledger === "absent" ? "no live ledger on this machine yet — the pilot has not traded here" : `live ledger read failed: ${d.ledgerError ?? "unknown"}`}
        </p>
      )}
      {lastCompleted && (
        <p className="muted" style={{ margin: 0 }}>
          Showing <strong>{d.session}</strong>, the last session the pilot settled — today's session has not opened yet.
        </p>
      )}
      <section>
        <div className="stats-grid">
          {d && (
            <>
              <PeriodTile label={lastCompleted ? `session ${d.session.slice(5)}` : "today"} p={d.periods.today} />
              <PeriodTile label="week" p={d.periods.week} />
              <PeriodTile label="month" p={d.periods.month} />
              <PeriodTile label="year" p={d.periods.year} />
              <div className="stat-tile" title="the latest tick's summed mid mark across open positions — a mid, not a fill">
                <span className="stat-label">now · mark (mid)</span>
                <span className={`stat-value ${tone(d.today.markPnl)}`}>{signed(d.today.markPnl)}</span>
                <span className="muted" style={{ display: "block", fontSize: 11 }}>
                  {d.today.markedAt !== null ? `at ${hhmmOf(d.today.markedAt)}` : "no mark yet"}
                  {d.today.maxDrawdown !== null ? ` · drawdown ${fmtMoney(d.today.maxDrawdown)}` : ""}
                </span>
              </div>
              {heldPeak !== null ? (
                // Settled: the last tick's exposure is the book just before the bell, which beside
                // "0 open" reads as risk still on. The session's peak is what the day carried.
                <div className="stat-tile" title="the book is settled: the largest worst case at expiry it carried during the session, from the loop's own per-tick exposure — shown until the next session opens">
                  <span className="stat-label">worst case at expiry · session peak / cap</span>
                  <span className={`stat-value ${bp && bp.cap && -heldPeak.worst > bp.cap ? "pnl-neg" : ""}`}>
                    {`${fmtMoney(-heldPeak.worst)} / ${bp?.cap == null ? "no cap" : fmtMoney(bp.cap)}`}
                  </span>
                  <span className="muted" style={{ display: "block", fontSize: 11 }}>
                    at {hhmmOf(heldPeak.at)} · settled · until the next open ·{" "}
                    {d.today.completionPct !== null ? `${d.today.completionPct.toFixed(0)}% complete` : "no completions"}
                  </span>
                </div>
              ) : (
                <div className="stat-tile" title="every open position's own worst case, net of fees and the assignment reserve — the same figure the buying-power cap reads">
                  <span className="stat-label">open exposure / cap</span>
                  <span className={`stat-value ${bp && bp.cap && bp.open > bp.cap ? "pnl-neg" : ""}`}>
                    {bp ? `${fmtMoney(bp.open)} / ${bp.cap === null ? "no cap" : fmtMoney(bp.cap)}` : "—"}
                  </span>
                  <span className="muted" style={{ display: "block", fontSize: 11 }}>
                    {capPct !== null ? `${capPct.toFixed(0)}% used · ` : ""}
                    {d.today.open} open · {d.today.pending} working · {d.today.completionPct !== null ? `${d.today.completionPct.toFixed(0)}% complete` : "no completions"}
                  </span>
                </div>
              )}
              <div className="stat-tile" title={acct ? `broker account ${acct.account}, as of ${acct.at} — whole account, every module and manual trade included; a mid is not a fill` : bp?.accountError ?? "broker read unavailable"}>
                <span className="stat-label">account · broker</span>
                <span className="stat-value">
                  {acct?.derivativeBuyingPower !== null && acct?.derivativeBuyingPower !== undefined ? fmtMoney(acct.derivativeBuyingPower) : "—"}
                  <span className="muted" style={{ fontSize: 11 }}> BP free</span>
                </span>
                <span className="muted" style={{ display: "block", fontSize: 11 }}>
                  {acct
                    ? `NLV ${acct.netLiquidatingValue !== null ? fmtMoney(acct.netLiquidatingValue) : "—"} · day ${acct.dayPl !== null ? fmtMoney(acct.dayPl) : "—"} · ${acct.legCount ?? 0} legs${acct.unpricedCount ? `, ${acct.unpricedCount} unpriced` : ""}`
                    : (bp?.accountError ?? "unavailable")}
                </span>
              </div>
            </>
          )}
        </div>
      </section>
      <Card title={`intraday · ${d?.session ?? ""} · mark is a mid, not a fill`} updatedAt={dataUpdatedAt} isError={isError}>
        {d ? <LiveChart data={d} /> : <p className="muted">loading</p>}
      </Card>
      <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(28rem, 1fr))" }}>
        <DataCard
          title="live positions"
          headers={["entered", "structure", "state", "credit", "floor", "mark", "P&L"]}
          loading={isLoading}
          isError={isError}
          empty="nothing entered today"
          rowCount={d?.positions.length ?? 0}
          numFrom={3}
          updatedAt={dataUpdatedAt}
        >
          {d?.positions.map((p) => (
            <tr key={p.positionId}>
              <td>{hhmmOf(p.entryTime)}</td>
              <td>{structure(p)}</td>
              <td>{fillState(p)}</td>
              <td>{p.netCredit.toFixed(2)}</td>
              <td className={tone(p.floorDollars)}>{p.floorDollars !== null ? fmtMoney(p.floorDollars) : "—"}</td>
              <td className={tone(p.mark?.markPnl)} title={p.mark ? `mid ${p.mark.structureMid.toFixed(2)} at ${hhmmOf(p.mark.at)}` : "no usable mark"}>
                {p.mark ? fmtMoney(p.mark.markPnl) : "—"}
              </td>
              <td className={tone(p.pnl)}>{p.pnl !== null ? fmtMoney(p.pnl) : ""}</td>
            </tr>
          ))}
        </DataCard>
        <DataCard
          title="activity"
          headers={["last", "mode", "reason", "×", "centre", "detail"]}
          loading={isLoading}
          isError={isError}
          empty="no decisions journaled today"
          rowCount={d?.feed.length ?? 0}
          numFrom={3}
          updatedAt={dataUpdatedAt}
          footer={
            d !== undefined && d.feed.length > ACTIVITY_PAGE_SIZES[0] && (
              <Pager
                offset={activity.page.offset}
                limit={activity.page.limit}
                total={d.feed.length}
                pageSizes={ACTIVITY_PAGE_SIZES}
                onOffset={activity.setOffset}
                onLimit={activity.setLimit}
              />
            )
          }
        >
          {d?.feed.slice(activity.page.offset, activity.page.offset + activity.page.limit).map((r, i) => (
            <tr key={activity.page.offset + i} className={r.accepted ? "" : "muted"}>
              <td>{hhmmOf(r.lastSeen)}</td>
              <td>{r.mode}</td>
              <td>{r.reason}</td>
              <td>{r.occurrences}</td>
              <td>{r.centerLast ?? ""}</td>
              <td>{r.detail ?? ""}</td>
            </tr>
          ))}
        </DataCard>
      </div>
      {d && <PerformanceSection perf={d.performance} />}
    </div>
  );
}
