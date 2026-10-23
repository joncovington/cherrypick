import type { TradingMode } from "@console/shared";
import { useEarningsLive, type EarningsEvent, type EarningsOpenPosition } from "../../lib/api";
import { DataCard, PnlCell, fmtMoney, sortRows, useSort, type SortHeader } from "../../components/DataTable";
import { fmtCash, fmtPrice } from "../../lib/format";

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
function LoopBar({ mode }: { mode: TradingMode }) {
  const { data } = useEarningsLive(mode);
  const loop = data?.loop;
  if (!loop) {
    // The live ledger has no loop_iterations table, so live never has a tick to show.
    if (mode === "live") return <span className="chip">live ledger: no marks or loop ticks recorded</span>;
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

const POSITION_HEADERS: Array<string | SortHeader> = [
  { label: "Symbol", k: "symbol" },
  { label: "Strategy", k: "strategy" },
  { label: "Opened", k: "opened" },
  { label: "Qty", k: "qty" },
  { label: "Price (per share)", k: "price" },
  { label: "Entry", k: "entry" },
  { label: "Mark (per share)", k: "mark" },
  { label: "To close", k: "close" },
  { label: "Unrealized (gross)", k: "unrealised" },
  "Best / worst",
  "Waiting on",
];

const POSITION_SORTS = {
  symbol: (p: EarningsOpenPosition) => p.symbol,
  strategy: (p: EarningsOpenPosition) => p.strategy,
  opened: (p: EarningsOpenPosition) => p.openedAt,
  qty: (p: EarningsOpenPosition) => p.quantity,
  price: (p: EarningsOpenPosition) => p.entryCredit,
  entry: (p: EarningsOpenPosition) => p.entryCash,
  // An unpriced mark is not a valuation, so it sinks with the nulls rather than ranking as a number.
  mark: (p: EarningsOpenPosition) => p.mark?.exitDebit ?? null,
  close: (p: EarningsOpenPosition) => p.mark?.exitCash ?? null,
  unrealised: (p: EarningsOpenPosition) => p.mark?.unrealizedPnl ?? null,
};

export function EarningsLiveCard({ mode }: { mode: TradingMode }) {
  const { data, isLoading, isError } = useEarningsLive(mode);
  const sort = useSort();
  const positions = sortRows(data?.positions ?? [], sort, POSITION_SORTS);

  return (
    <DataCard
      title={
        positions.length
          ? `Open positions — ${mode} book, ${fmtMoney(data?.openCapital ?? 0)} at risk`
          : `Open positions — ${mode} book`
      }
      headers={POSITION_HEADERS}
      sort={sort}
      loading={isLoading}
      isError={isError}
      rowCount={positions.length}
      numFrom={3}
      empty="Nothing carrying risk. The 15:45 ET scan opens entries; a winner may then be held up to three sessions, a loser closes on the first morning."
      controls={<LoopBar mode={mode} />}
    >
      {positions.map((p) => (
        <tr key={p.orderId}>
          <td>{p.symbol}</td>
          <td>{p.strategy}</td>
          <td title={p.openedAt ?? ""}>{ago(p.openedAt)}</td>
          <td>{p.quantity ?? "—"}</td>
          {/* Per share is the one per-share column; the rest are whole-position dollars, signed. */}
          <td>{fmtPrice(p.entryCredit)}</td>
          <td>{fmtCash(p.entryCash)}</td>
          {/* A refused mark records that we looked and could not price it — real, but not a
              valuation, so it is never shown as one. The mark is a cost to close, so a debit. */}
          <td title={p.mark ? `${p.mark.source ?? "?"} · ${ago(p.mark.markedAt)}` : ""}>
            {p.mark && p.mark.exitDebit !== null ? (
              fmtPrice(-p.mark.exitDebit)
            ) : (
              <span className="muted">unpriced</span>
            )}
          </td>
          <td>{fmtCash(p.mark?.exitCash ?? null)}</td>
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
