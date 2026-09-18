import { useQuery } from "@tanstack/react-query";
import type { LiveFliesPayload, LivePeriod, LivePosition } from "@console/shared";
import { Card, DataCard } from "../../components/DataTable";
import { fmtMoney } from "../../lib/format";
import { LiveChart } from "./LiveChart";

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

function PeriodTile({ label, p }: { label: string; p: LivePeriod }) {
  return (
    <div className="stat-tile" title={`settled net over ${p.sessions} session${p.sessions === 1 ? "" : "s"}, ${p.trades} structure${p.trades === 1 ? "" : "s"}; paper control the same period: ${p.paperNet === null ? "n/a" : fmtMoney(p.paperNet)}`}>
      <span className="stat-label">{label} · settled</span>
      <span className={`stat-value ${tone(p.trades > 0 ? p.net : null)}`}>{p.trades > 0 ? fmtMoney(p.net) : "—"}</span>
      <span className="muted" style={{ display: "block", fontSize: 11 }}>
        {p.trades} live · paper {p.paperNet === null ? "—" : fmtMoney(p.paperNet)}
      </span>
    </div>
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

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.8rem" }}>
      {d && d.ledger !== "ok" && (
        <p className={d.ledger === "failed" ? "pnl-neg" : "muted"}>
          {d.ledger === "absent" ? "no live ledger on this machine yet — the pilot has not traded here" : `live ledger read failed: ${d.ledgerError ?? "unknown"}`}
        </p>
      )}
      <section>
        <div className="stats-grid">
          {d && (
            <>
              <PeriodTile label="today" p={d.periods.today} />
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
              <td>{p.net.toFixed(2)}</td>
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
        >
          {d?.feed.map((r, i) => (
            <tr key={i} className={r.accepted ? "" : "muted"}>
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
    </div>
  );
}
