import { useEarningsLive, type EarningsEvent, type EarningsOpenPosition } from "../../lib/api";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";

/**
 * What the managed loop is holding right now.
 *
 * Before positions were managed there was nothing to put here: everything opened one afternoon and
 * was force-closed the next morning, so an open position was never more than a few hours old and
 * never had a decision made about it. A winner can now be carried up to three sessions, so what it
 * is worth mid-flight, and whether the loop marking it is even alive, are the two things this page
 * exists to answer.
 */

function ago(stamp: string | null): string {
  if (!stamp) return "—";
  const seconds = (Date.now() - new Date(stamp).getTime()) / 1000;
  if (!Number.isFinite(seconds)) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

/** A loop that ticks every minute is stale the moment it stops, so this leads with when it last ran
 *  rather than with what it found. */
function LoopBar() {
  const { data } = useEarningsLive();
  const loop = data?.loop;
  if (!loop) {
    return <span className="chip">loop: no ticks yet today (it records a row only in session)</span>;
  }
  return (
    <>
      <span className={loop.status === "ok" ? "chip chip-ok" : "chip chip-warn"}>
        {loop.phase ?? "unknown"} · {loop.status ?? "?"}
      </span>
      <span className="chip">last tick {ago(loop.ranAt)}</span>
      {loop.marksWritten ? <span className="chip">{loop.marksWritten} marked</span> : null}
      {loop.actionsTaken ? <span className="chip chip-ok">{loop.actionsTaken} acted</span> : null}
      {loop.quotesStale ? <span className="chip chip-warn">{loop.quotesStale} stale quotes</span> : null}
      {loop.note ? <span className="chip chip-warn">{loop.note}</span> : null}
    </>
  );
}

function waitingOn(position: EarningsOpenPosition): string {
  const event = position.lastEvent;
  if (!event) return "not yet evaluated";
  if (event.executed) return `${event.reason} (acted)`;
  // A gated decision is the interesting case: the exit was SEEN and held back, and saying so is
  // what keeps a later exit from reading as a late reaction.
  if (event.gate) return `${event.reason} — held by ${event.gate}`;
  return event.reason;
}

const POSITION_HEADERS = [
  "Symbol",
  "Strategy",
  "Opened",
  "Credit",
  "Mark",
  "Unrealized",
  "Best / worst",
  "Waiting on",
];

export function EarningsLiveCard() {
  const { data, isLoading, isError } = useEarningsLive();
  const positions = data?.positions ?? [];

  return (
    <DataCard
      title={
        positions.length
          ? `Open positions — ${fmtMoney(data?.openCapital ?? 0)} at risk`
          : "Open positions"
      }
      headers={POSITION_HEADERS}
      loading={isLoading}
      isError={isError}
      rowCount={positions.length}
      numFrom={3}
      empty="Nothing carrying risk. The 15:45 ET scan opens entries; a winner may then be held up to three sessions, a loser closes on the first morning."
      controls={<LoopBar />}
    >
      {positions.map((p) => (
        <tr key={p.orderId}>
          <td>{p.symbol}</td>
          <td>{p.strategy}</td>
          <td title={p.openedAt ?? ""}>{ago(p.openedAt)}</td>
          <td>{fmtNum(p.entryCredit)}</td>
          {/* A refused mark records that we looked and could not price it — real, but not a
              valuation, so it is never shown as one. */}
          <td title={p.mark ? `${p.mark.source ?? "?"} · ${ago(p.mark.markedAt)}` : ""}>
            {p.mark && p.mark.exitDebit !== null ? (
              fmtNum(p.mark.exitDebit)
            ) : (
              <span className="muted">unpriced</span>
            )}
          </td>
          <td>
            <PnlCell v={p.mark?.unrealizedPnl ?? null} />
          </td>
          <td className="muted">
            {fmtMoney(p.maxUnrealizedPnl ?? null)} / {fmtMoney(p.minUnrealizedPnl ?? null)}
          </td>
          <td>{waitingOn(p)}</td>
        </tr>
      ))}
    </DataCard>
  );
}

function outcome(event: EarningsEvent): string {
  if (event.executed) return "acted";
  return event.gate ? `held: ${event.gate}` : "held";
}

const EVENT_HEADERS = ["When", "Order", "Phase", "Action", "Reason", "Outcome"];

export function EarningsManagementLog() {
  const { data, isLoading, isError } = useEarningsLive();
  const events = data?.events ?? [];

  return (
    <DataCard
      title="Management log"
      headers={EVENT_HEADERS}
      loading={isLoading}
      isError={isError}
      rowCount={events.length}
      empty="No decisions recorded yet — the loop records one per position per tick, in session."
    >
      {events.map((e, i) => (
        <tr key={`${e.orderId}-${e.occurredAt ?? i}`}>
          <td title={e.occurredAt ?? ""}>{ago(e.occurredAt)}</td>
          <td className="muted">{e.orderId}</td>
          <td>{e.phase ?? "—"}</td>
          <td>{e.action}</td>
          <td>{e.reason}</td>
          <td className={e.executed ? "pnl-pos" : "muted"}>{outcome(e)}</td>
        </tr>
      ))}
    </DataCard>
  );
}
