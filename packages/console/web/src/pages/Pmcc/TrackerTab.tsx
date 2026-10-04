import { Link, useSearchParams } from "react-router-dom";
import type {
  PmccTracker,
  PmccTrackerIndexRow,
  PmccTrackerShort,
  PmccTrackerWeek,
} from "@console/shared";
import { usePmccTracker, usePmccTrackerIndex } from "../../lib/api";
import { Card, DataCard, PnlCell } from "../../components/DataTable";
import { TileGrid } from "../../components/performance/TileGrid";
import { Tile } from "../../components/performance/Tile";
import { HistoryTable } from "../../components/table/HistoryTable";
import type { ColumnDef } from "../../components/table/columns";
import { TimeLineChart } from "../../components/chart/TimeLineChart";
import { fmtCash, fmtMoney, fmtNum, fmtPct, fmtPctSigned, fmtPrice } from "../../lib/format";
import { fmtStrike } from "../../lib/optionFormat";

/**
 * One PMCC position, week by week -- the spreadsheet a covered-call trader keeps (Tom King's layout,
 * the 2026-10-04 request), read from the module's own `analytics.tracker` through the bridge.
 *
 * A held-long position lives ~10 months and sells ~40 weekly shorts against one long, and the
 * closed-position tables say nothing about it until its long is sold. This page is that position
 * the whole way: what it is worth now, the long, the short it holds, every short it has sold, and
 * one row per week valued at that week's close (the last row IS the header -- one valuation rule,
 * `tracker.value_at`, behind both).
 *
 * Addressed by `?position=`, which the module frame carries across tabs, so a position is a
 * shareable page rather than an overlay. With no position, the page lists every one it can open.
 */
export function TrackerTab({ symbol }: { symbol: string | null }) {
  const [params] = useSearchParams();
  const position = params.get("position");
  const index = usePmccTrackerIndex();
  const tracker = usePmccTracker(position);
  const rows = (index.data?.data ?? []).filter((r) => symbol === null || r.symbol === symbol);
  return (
    <div className="cards cards-wide">
      {position === null ? (
        <TrackerIndex rows={rows} loading={index.isLoading} error={index.data?.ok === false ? index.data.error : null} />
      ) : tracker.isLoading ? (
        <Card title={position}>
          <p className="muted">loading…</p>
        </Card>
      ) : tracker.data?.ok !== true || tracker.data.data === null ? (
        <Card title={position} isError>
          <p className="muted">
            {tracker.data?.error ?? "this position could not be read"} · <Link to="?">every position</Link>
          </p>
        </Card>
      ) : (
        <TrackerView t={tracker.data.data} updatedAt={tracker.dataUpdatedAt} />
      )}
    </div>
  );
}

function positionLink(id: string): string {
  return `?position=${encodeURIComponent(id)}`;
}

export function TrackerIndex({
  rows,
  loading,
  error,
}: {
  rows: PmccTrackerIndexRow[];
  loading: boolean;
  error: string | null;
}) {
  return (
    <DataCard
      title="positions"
      headers={["position", "lifecycle", "status", "opened", "closed", "shorts", "net"]}
      loading={loading}
      isError={error !== null}
      rowCount={rows.length}
      numFrom={5}
      empty={error ?? "no position on file yet"}
    >
      {rows.map((r) => (
        <tr key={r.positionId}>
          <td>
            <Link to={positionLink(r.positionId)}>
              {r.symbol} · <span className="mono">{r.arm}</span>
            </Link>
          </td>
          <td>{r.lifecycle === "held_long" ? "held long" : "weekly"}</td>
          <td>{r.status}</td>
          <td>{r.entrySession}</td>
          <td>{r.closedSession ?? "—"}</td>
          <td>{r.shorts}</td>
          <td title={r.status === "closed" ? "realised: gross - every cost" : "marked to market: net of costs to date"}>
            <PnlCell v={r.net} />
          </td>
        </tr>
      ))}
    </DataCard>
  );
}

const pct = (v: number | null) => fmtPctSigned(v === null ? null : v * 100, 2);
const tone = (v: number | null) => (v === null ? undefined : v > 0 ? "pos" : v < 0 ? "neg" : undefined);

export function TrackerView({ t, updatedAt }: { t: PmccTracker; updatedAt?: number }) {
  const h = t.header;
  const p = t.position;
  const costs = h.costs;
  const shortTotal =
    h.shortRealised === null || h.shortOpen === null ? null : Math.round((h.shortRealised + h.shortOpen) * 100) / 100;
  return (
    <>
      <Card title={`${p.symbol} · ${p.arm} · ${p.status === "closed" ? `closed ${p.closedSession ?? ""}` : "open"}`} updatedAt={updatedAt}>
        <p className="muted" style={{ marginTop: 0 }}>
          {p.lifecycle === "held_long" ? "held long" : "weekly"} · opened {p.entrySession} at {fmtNum(p.entrySpot, 2)} ·{" "}
          {h.shortsSold} short{h.shortsSold === 1 ? "" : "s"} sold · era {p.era ?? "—"} · <Link to="?">every position</Link>
        </p>
        <TileGrid count={12}>
          <Tile label="long cost" value={fmtCash(h.longCost)} title="what the long call cost, a debit" />
          <Tile label="long value" value={fmtMoney(h.longValue)} title="the long at its latest usable mark" />
          <Tile label="long gain / loss" value={fmtCash(h.longGain)} tone={tone(h.longGain)} />
          <Tile
            label="short calls"
            value={fmtCash(shortTotal)}
            tone={tone(shortTotal)}
            title={`realised ${fmtCash(h.shortRealised)} · open ${fmtCash(h.shortOpen)}${h.shares ? ` · shares ${fmtCash(h.shares)}` : ""}`}
          />
          <Tile
            label="costs"
            value={fmtCash(costs?.total === null || costs?.total === undefined ? null : -costs.total)}
            title={`fees ${fmtMoney(costs?.fees ?? null)} · slippage ${fmtMoney(costs?.slippage ?? null)} (charged as a cost in this module, and subtracted) · settlement ${costs?.settlement === null ? "n/r" : fmtMoney(costs?.settlement ?? null)}`}
          />
          <Tile label="net P&L" value={fmtCash(h.net)} tone={tone(h.net)} afterFees />
          <Tile label="return on long cost" value={pct(h.returnOnLongCost)} tone={tone(h.returnOnLongCost)} title="net ÷ what the long cost — how the strategy's promoters quote it" />
          <Tile label="return on notional" value={pct(h.returnOnNotional)} tone={tone(h.returnOnNotional)} title="net ÷ the stock value the position controls at entry (spot × 100 × qty)" />
          <Tile label="underlying since open" value={pct(h.underlyingSinceOpen)} />
          <Tile label="days in trade" value={h.daysInTrade === null ? "—" : String(h.daysInTrade)} />
          <Tile label="net delta" value={h.netDelta === null ? "—" : `${fmtNum(h.netDelta, 0)} sh`} title="long delta minus short delta, in shares" />
          <Tile
            label="net extrinsic"
            value={fmtCash(h.netExtrinsic)}
            tone={tone(h.netExtrinsic)}
            title={`extrinsic captured on the shorts ${fmtCash(h.extrinsicCaptured)} less the long's own extrinsic decay ${fmtMoney(h.longExtrinsicDecay)} — what the structure actually harvested`}
          />
        </TileGrid>
        {(t.integrity.weeksWithoutAShort.length > 0 || t.integrity.unpricedWeeks > 0 || (t.integrity.exposureTicks ?? 0) > 0) && (
          <p className="integrity-note">
            {t.integrity.weeksWithoutAShort.length > 0 && (
              <>weeks with no short open at the close: {t.integrity.weeksWithoutAShort.join(", ")}. </>
            )}
            {t.integrity.unpricedWeeks > 0 && <>{t.integrity.unpricedWeeks} week(s) could not be priced and show a dash. </>}
            {(t.integrity.exposureTicks ?? 0) > 0 && (
              <>The short sat in the early-assignment region on {t.integrity.exposureTicks} marked tick(s) — unmodelled, so this is an upper bound. </>
            )}
          </p>
        )}
      </Card>

      <div className="cards-pairs">
        <CurrentShortCard t={t} />
        <LongLotsCard t={t} />
      </div>

      <HistoryTable<PmccTrackerShort>
        table="pmcc-tracker-shorts"
        title="short-call log"
        defs={SHORT_COLUMNS}
        rows={t.shorts}
        rowKey={(r) => r.legRole}
        loading={false}
        empty="no short sold yet"
        dateRange={false}
        footer={
          <span className="muted">
            Each row is one short: entry + exit = gross; gross − fees − slip = net. An open short's exit is its mark.
          </span>
        }
      />

      <WeeksCard weeks={t.weeks} />
    </>
  );
}

function CurrentShortCard({ t }: { t: PmccTracker }) {
  const s = t.currentShort;
  return (
    <Card title="current short">
      {s === null ? (
        <p className="muted">no short open{t.position.status === "closed" ? " — the position is closed" : " — the next tick sells one"}</p>
      ) : (
        <table className="data-table num-from-1">
          <tbody>
            <tr><td>strike · expiry</td><td>{fmtStrike(s.strike)} · {s.expiration} ({s.dte} DTE)</td></tr>
            <tr><td>stock at sale</td><td>{fmtNum(s.stockAtSale, 2)}</td></tr>
            <tr><td>premium</td><td>{fmtPrice(s.premium)}</td></tr>
            <tr><td>intrinsic · extrinsic at sale</td><td>{fmtNum(s.intrinsicAtSale, 2)} · {fmtNum(s.extrinsicAtSale === null ? null : s.extrinsicAtSale / 100 / Math.max(1, t.position.quantity), 2)}</td></tr>
            <tr><td>extrinsic sold</td><td>{fmtCash(s.extrinsicAtSale)}</td></tr>
            <tr><td>extrinsic left</td><td>{fmtMoney(s.extrinsicNow)} {s.decayedPct !== null && <span className="muted">({fmtPct(s.decayedPct * 100, 0)} decayed)</span>}</td></tr>
            <tr><td title="extrinsic sold ÷ what the long cost — the weekly figure the strategy targets at ~1%">extrinsic ÷ long cost</td><td>{s.extrinsicOnLongCostPct === null ? "—" : fmtPct(s.extrinsicOnLongCostPct * 100, 2)}</td></tr>
            <tr><td title="stock at sale minus the premium">breakeven</td><td>{fmtNum(s.breakeven, 2)}</td></tr>
            <tr><td>rolls at</td><td>{s.next.expiryRollAt}</td></tr>
            {s.next.decayRollBelow !== null && <tr><td>early roll when extrinsic is under</td><td>{fmtMoney(s.next.decayRollBelow)}</td></tr>}
            {s.next.breachAt !== null && <tr><td>rolls down if spot reaches</td><td>{fmtStrike(s.next.breachAt)}</td></tr>}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function LongLotsCard({ t }: { t: PmccTracker }) {
  return (
    <DataCard
      title="core position"
      headers={["opened", "expiry", "stock", "strike", "qty", "cost", "value", "gain / loss", "extrinsic paid", "extrinsic now", "delta"]}
      loading={false}
      rowCount={t.longLots.length}
      numFrom={2}
      empty="no long on file"
    >
      {t.longLots.map((l) => (
        <tr key={`${l.opened}-${l.strike}`}>
          <td>{l.opened}</td>
          <td>{l.expiration}</td>
          <td>{fmtNum(l.spotOpen, 2)}</td>
          <td>{fmtStrike(l.strike)}</td>
          <td>{l.quantity}</td>
          <td>{fmtCash(l.cost)}</td>
          <td>{fmtMoney(l.valueNow)}</td>
          <td><PnlCell v={l.gain} /></td>
          <td>{fmtMoney(l.extrinsicPaid)}</td>
          <td>{fmtMoney(l.extrinsicNow)}</td>
          <td>{fmtNum(l.deltaNow, 2)}</td>
        </tr>
      ))}
    </DataCard>
  );
}

function WeeksCard({ weeks }: { weeks: PmccTrackerWeek[] }) {
  const points = weeks.filter((w) => w.net !== null).map((w) => ({ x: w.weekEnd, y: w.net as number }));
  return (
    <DataCard
      title="week by week"
      headers={["week", "week end", "spot", "long", "short open", "shorts realised", "costs", "net to date", "change", "on long cost", "on notional", "net delta"]}
      loading={false}
      rowCount={weeks.length}
      numFrom={2}
      empty="no week yet"
      footer={
        points.length >= 2 ? (
          <TimeLineChart series={[{ label: "net to date", points, fill: "rgba(80,160,255,0.12)" }]} height={180} />
        ) : (
          <span className="muted">Each week is valued at its own close; the last row is now, and is the header above.</span>
        )
      }
    >
      {weeks.map((w) => (
        <tr key={w.week}>
          <td>{w.week}</td>
          <td>{w.weekEnd}</td>
          <td>{fmtNum(w.spot, 2)}</td>
          <td>{fmtNum(w.longMark, 2)}</td>
          <td>{fmtCash(w.shortOpen)}</td>
          <td>{fmtCash(w.shortRealised)}</td>
          <td>{fmtCash(w.costs === null ? null : -w.costs)}</td>
          <td><PnlCell v={w.net} /></td>
          <td>{fmtCash(w.change)}</td>
          <td>{pct(w.returnOnLongCost)}</td>
          <td>{pct(w.returnOnNotional)}</td>
          <td>{w.netDelta === null ? "—" : fmtNum(w.netDelta, 0)}</td>
        </tr>
      ))}
    </DataCard>
  );
}

const SHORT_COLUMNS: ColumnDef<PmccTrackerShort>[] = [
  { id: "n", header: "#", kind: "describe", numeric: true, render: (r) => r.n },
  { id: "opened", header: "opened", kind: "describe", render: (r) => r.opened ?? "—" },
  { id: "closed", header: "closed", kind: "describe", render: (r) => r.closed ?? "—" },
  { id: "days", header: "days", kind: "describe", numeric: true, render: (r) => r.daysHeld ?? "—" },
  {
    id: "stock",
    header: "stock",
    title: "spot when the short was sold → when it was closed (or now)",
    kind: "describe",
    numeric: true,
    render: (r) => `${fmtNum(r.spotOpen, 2)} → ${fmtNum(r.spotClose, 2)}`,
  },
  { id: "strike", header: "strike", kind: "describe", numeric: true, render: (r) => fmtStrike(r.strike) },
  { id: "expiry", header: "expiry", kind: "describe", render: (r) => r.expiration },
  { id: "sold", header: "sold", title: "per-share premium received", kind: "describe", numeric: true, render: (r) => fmtPrice(r.sold) },
  {
    id: "bought",
    header: "bought",
    title: "per-share buyback (or mark, if still open)",
    kind: "describe",
    numeric: true,
    render: (r) => fmtPrice(r.bought === null ? null : -r.bought),
  },
  {
    id: "extrinsic",
    header: "extrinsic captured",
    title: "extrinsic sold minus extrinsic left at the close, whole-position dollars",
    kind: "describe",
    numeric: true,
    render: (r) => fmtCash(r.extrinsicCaptured),
  },
  { id: "how", header: "how", kind: "describe", render: (r) => r.how ?? "—" },
  { id: "why", header: "why", kind: "describe", className: "mono", render: (r) => r.why ?? "—" },
  { id: "entry", header: "entry", title: "premium received, a credit", kind: "money", render: (r) => fmtCash(r.entry) },
  { id: "exit", header: "exit", title: "buyback paid, a debit (an open short's mark)", kind: "money", render: (r) => fmtCash(r.exit) },
  { id: "gross", header: "gross", title: "entry + exit", kind: "money", render: (r) => fmtCash(r.gross) },
  { id: "fees", header: "fees", title: "commissions and exchange fees on this short's own tickets", kind: "money", render: (r) => (r.fees === null ? "n/r" : fmtMoney(-r.fees)) },
  {
    id: "slip",
    header: "slip",
    title: "slippage: charged as a cost in this module, and subtracted",
    kind: "money",
    render: (r) => (r.slippage === null ? "n/r" : fmtMoney(-r.slippage)),
  },
  { id: "net", header: "net", title: "gross - fees - slip", kind: "money", pinned: true, render: (r) => <PnlCell v={r.net} /> },
];
