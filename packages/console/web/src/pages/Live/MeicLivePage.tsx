import { useQuery } from "@tanstack/react-query";
import type { LiveMeicOpenRow, LiveMeicPayload } from "@console/shared";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";
import { TileGrid } from "../../components/performance/TileGrid";
import { HistoryTable } from "../../components/table/HistoryTable";
import { fmtCash, fmtPrice } from "../../lib/format";
import { Tile } from "../Flies/PerformanceTab";
import { HISTORY_COLUMNS } from "../Meic/MeicTables";

/**
 * MEIC's live loop, read-only (2026-10-15): the flies Live tab's arming strip and trade tables, cut
 * to what MEIC's live loop records. It keeps no per-tick marks and no decision journal, so there is
 * no intraday curve and no activity feed; the loop's own `--status` keys the watchdog reads
 * (armed_for, orphaned orders, held submissions, broker health) are read here from the same files.
 * There is no button here that touches an order.
 *
 * Money is the MEIC page's: the same server derivation and the same history columns, less the
 * slippage column -- a live fill is the broker's price, and MEIC's slippage figure is a paper
 * model's concession.
 */
export function useLiveMeic() {
  return useQuery<LiveMeicPayload>({
    queryKey: ["live-meic"],
    queryFn: async () => {
      const res = await fetch("/api/live/meic");
      if (!res.ok) throw new Error(`live meic: HTTP ${res.status}`);
      return (await res.json()) as LiveMeicPayload;
    },
    refetchInterval: 15_000,
  });
}

const LIVE_HISTORY_COLUMNS = HISTORY_COLUMNS.filter((c) => c.id !== "slip");

function hhmmOf(ts: string | null): string {
  return ts !== null && ts.length >= 16 ? ts.slice(11, 16) : "—";
}

function sides(r: LiveMeicOpenRow): string {
  if (r.working) return "—";
  const side = (name: string, held: boolean, closing: boolean) => (closing ? `${name} closing` : held ? name : null);
  const held = [side("put", r.putHeld, r.putClosing), side("call", r.callHeld, r.callClosing)].filter((s) => s !== null);
  return held.length === 0 ? "none" : held.join(" + ");
}

function state(r: LiveMeicOpenRow): string {
  if (r.working) return "entry working";
  return r.overdue ? `${r.status} · overdue` : r.status;
}

function armLabel(d: LiveMeicPayload): { value: string; tone?: "pos" | "neg" | "dim" } {
  const a = d.arm;
  if (a.armed && a.pastDisarm) return { value: "armed · past disarm", tone: "dim" };
  if (a.armed) return { value: "ARMED", tone: "pos" };
  if (a.stale) return { value: "stale arm", tone: "dim" };
  return { value: "not armed", tone: "dim" };
}

export function MeicLivePage() {
  const { data: d, isLoading, isError, dataUpdatedAt } = useLiveMeic();
  const arm = d !== undefined ? armLabel(d) : null;
  const health = d?.brokerHealth ?? null;
  const heldCount = d !== undefined ? d.held.unresolved.length + d.held.unrecorded.length : 0;
  const t = d?.totals;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.8rem" }}>
      {d && d.ledger !== "ok" && (
        <p className={d.ledger === "failed" ? "pnl-neg" : "muted"}>
          {d.ledger === "absent"
            ? "no MEIC live ledger on this machine yet — the live loop has not traded here"
            : `live ledger read failed: ${d.ledgerError ?? "unknown"}`}
        </p>
      )}
      {d && (
        <section className="card">
          <TileGrid count={6}>
            <Tile
              label={`arm · ${d.session}`}
              value={arm?.value ?? "—"}
              tone={arm?.tone}
              title={
                `arm record ${d.arm.date ?? "none"}${d.arm.at !== null ? ` (written ${d.arm.at})` : ""}; armed means dated today. ` +
                `The loop's next tick deletes a record that is stale or past the disarm time (${d.arm.disarmTime} ET).`
              }
            />
            <Tile
              label="disarm time"
              value={`${d.arm.disarmTime} ET`}
              title="live.disarm_time in MEIC's config, else the loop's default 17:00"
            />
            <Tile
              label="halt flag"
              value={d.arm.halted ? "HALTED" : "clear"}
              tone={d.arm.halted ? "neg" : "dim"}
              title="state/halt-live.flag: blocks new ICs only; fills are still confirmed and open ICs still stopped and closed. Clearing it arms nothing."
            />
            <Tile
              label="live gate · symbol"
              value={`${d.arm.liveEnabled === null ? "unknown" : d.arm.liveEnabled ? "on" : "off"} · ${d.arm.symbol ?? "?"}`}
              title="enable_live_trading and live.symbol from MEIC's config; unknown means the config could not be read, never off"
            />
            <Tile
              label="orphaned orders"
              value={String(d.orphans.orders.length)}
              tone={d.orphans.orders.length > 0 ? "neg" : "dim"}
              title={
                d.orphans.at !== null
                  ? `working broker orders no live ledger knows, as of the last sweep at ${d.orphans.at}`
                  : "no sweep recorded yet"
              }
            />
            <Tile
              label="held · broker"
              value={`${heldCount} held · ${health === null ? "no record" : health.failingSince !== null ? `failing ×${health.failures}` : "ok"}`}
              tone={heldCount > 0 || (health?.failingSince ?? null) !== null ? "neg" : "dim"}
              title={
                `held: submissions the broker never answered, or fills the ledger never recorded. ` +
                (health === null
                  ? "No broker-contact record yet."
                  : `Broker last ok ${health.lastOkAt ?? "never"}` +
                    (health.failingSince !== null ? `; failing since ${health.failingSince}: ${health.lastError ?? ""}` : ""))
              }
            />
          </TileGrid>
        </section>
      )}

      {d && (d.orphans.orders.length > 0 || heldCount > 0) && (
        <DataCard
          title="orphaned and held orders"
          headers={["kind", "order", "status", "symbol", "external id"]}
          loading={false}
          isError={isError}
          rowCount={d.orphans.orders.length + heldCount}
        >
          {d.orphans.orders.map((o, i) => (
            <tr key={`o-${i}`}>
              <td className="pnl-neg">orphan</td>
              <td>{o.orderId ?? "—"}</td>
              <td>{o.status ?? "—"}</td>
              <td>{o.underlyingSymbol ?? "—"}</td>
              <td className="muted">{o.externalIdentifier ?? "—"}</td>
            </tr>
          ))}
          {d.held.unresolved.map((ext) => (
            <tr key={`u-${ext}`}>
              <td className="pnl-neg">unresolved</td>
              <td>—</td>
              <td className="muted">no broker answer</td>
              <td>—</td>
              <td className="muted">{ext}</td>
            </tr>
          ))}
          {d.held.unrecorded.map((u) => (
            <tr key={`r-${u.ext}`}>
              <td className="pnl-neg">unrecorded</td>
              <td>{u.orderId}</td>
              <td className="muted">filled, not in the ledger</td>
              <td>—</td>
              <td className="muted">{u.ext}</td>
            </tr>
          ))}
        </DataCard>
      )}

      <DataCard
        title={`Open — ${d?.open.length ?? 0} live iron condor${d?.open.length === 1 ? "" : "s"}`}
        headers={["date", "time", "sym", "arm", "put", "call", "wing", "qty", "price", "entry", "state", "sides held"]}
        numFrom={4}
        loading={isLoading}
        isError={isError}
        rowCount={d?.open.length ?? 0}
        empty="no open live iron condors"
        updatedAt={dataUpdatedAt}
      >
        {d?.open.map((r) => (
          <tr key={r.id} className={r.working ? "muted" : ""}>
            <td className={r.overdue ? "pnl-neg" : ""}>{r.tradeDate}</td>
            <td className="muted">{hhmmOf(r.entryTime)}</td>
            <td>{r.symbol}</td>
            <td className="muted">{r.arm ?? "—"}</td>
            <td>{fmtNum(r.putStrike, 0)}</td>
            <td>{fmtNum(r.callStrike, 0)}</td>
            <td>{fmtNum(r.wingWidth, 0)}</td>
            <td>{fmtNum(r.quantity, 0)}</td>
            <td title={r.working ? "the limit asked — the order has not filled" : "net entry price per share, from the broker's fill"}>
              {r.working ? `limit ${fmtPrice(r.netCredit)}` : fmtPrice(r.netCredit)}
            </td>
            <td title={r.working ? "a working order has taken in nothing yet" : "entry cash flow for the whole position: + received"}>
              {fmtCash(r.entryCash)}
            </td>
            <td title={r.fillConfirmedAt !== null ? `fill confirmed ${r.fillConfirmedAt}` : undefined}>{state(r)}</td>
            <td title="a side with a recorded close cost is closed; 'closing' is a close order working at the broker">{sides(r)}</td>
          </tr>
        ))}
      </DataCard>

      <HistoryTable
        table="meic-live-history"
        title={`History — ${d?.history.length ?? 0} resolved live`}
        defs={LIVE_HISTORY_COLUMNS}
        rows={d?.history ?? []}
        rowKey={(r) => `live-${r.id}`}
        loading={isLoading}
        isError={isError}
        empty="no resolved live iron condors"
        updatedAt={dataUpdatedAt}
        dateRange={false}
        footer={
          t !== undefined &&
          t.trades > 0 && (
            <span
              className="chip"
              title={
                "Over every resolved live row. Gross − fees − settlement = net. " +
                (t.settlementFees === null
                  ? `Settlement was recorded on ${t.settlementTrades} of these ${t.trades} trades (${fmtMoney(t.settlementRecorded)}); the rest is inside fees.`
                  : "")
              }
            >
              net <PnlCell v={t.net} /> · {t.trades} trades · {t.sessions} session{t.sessions === 1 ? "" : "s"} · gross {fmtMoney(t.gross)} · fees{" "}
              {fmtMoney(t.fees)} · settlement{" "}
              {t.settlementFees === null ? `n/r (recorded on ${t.settlementTrades} of ${t.trades})` : fmtMoney(t.settlementFees)}
            </span>
          )
        }
      />
    </div>
  );
}
