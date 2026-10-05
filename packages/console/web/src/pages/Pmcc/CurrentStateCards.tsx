import { Link } from "react-router-dom";
import type { PmccArmCell, PmccOpenPosition, PmccPayload } from "@console/shared";
import { Card, PnlCell, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { UnrealisedPnlCell } from "../../components/UnrealisedPnlCell";
import { SignedBar } from "../../components/Charts";
import { dteOf, fmtStrike } from "../../lib/optionFormat";
import { fmtCash, fmtPrice } from "../../lib/format";
import { EntrySpreadCell } from "./EntrySpread";

/**
 * The arms the page knows by name, in the order it lists them: the held-long pair (the base trade
 * since 2026-10-06) and control (retired that day, its history kept). An advisor experiment's
 * `advised:<name>` synthetic arm is recognised by its prefix, never by being "not core": shield
 * read as an advised arm for a day because the list held control alone.
 */
const CORE_BOOKS = ["shield", "shield_hold", "control"];
const isAdvised = (arm: string) => arm.startsWith("advised:");

/**
 * A leg as strike and FULL expiry date. A held-long position's long expires about a year out, so a
 * month-day form read `09-17` for a long a year away and a short a week away alike.
 */
function strikeAt(strike: number | null, expiration: string | null, withDte = false): string {
  if (strike === null) return "—";
  if (expiration === null) return fmtStrike(strike);
  const dte = withDte ? dteOf(expiration) : null;
  return `${fmtStrike(strike)} · ${expiration}${dte === null ? "" : ` · ${String(dte)}d`}`;
}

/**
 * Time value remaining against the threshold that closes the position.
 *
 * The whole trade is this number decaying to the threshold, so it gets the proximity tint. A null
 * is rendered as an em-dash with its reason on the title, never as $0.00 — a zero here would read
 * as "closing right now", which is the opposite of "we have no usable mark".
 */
function TvCell({ tv, threshold }: { tv: number | null; threshold: number | null }) {
  if (tv === null) {
    return (
      <span className="muted" title="no usable short mark recorded — not the same as zero time value">
        —
      </span>
    );
  }
  const near = threshold !== null && tv <= threshold * 2;
  const at = threshold !== null && tv <= threshold;
  return (
    <span className={at ? "pmcc-tv-at" : near ? "pmcc-tv-near" : ""}>
      {fmtMoney(tv)}
      {threshold !== null && <span className="muted"> → {fmtMoney(threshold)}</span>}
    </span>
  );
}

function PositionRows({
  rows,
  params,
  heldLong,
}: {
  rows: PmccOpenPosition[];
  params: PmccPayload["params"];
  /** Held-long rows: the long carries its days to expiry, and control's time-value exit threshold,
   *  which never applies to them, is not drawn; the weekly yield and protection columns are control's. */
  heldLong: boolean;
}) {
  const settlementStyle = params.settlementStyle;
  return (
    <>
      {rows.map((p) => {
        const exposedShare = p.markedTicks > 0 ? (p.exposedTicks / p.markedTicks) * 100 : null;
        return (
          <tr key={p.positionId}>
            <td>
              <Link to={`/pmcc/tracker?position=${encodeURIComponent(p.positionId)}`} title="this position, week by week">
                {p.symbol}
              </Link>
            </td>
            <td>
              {p.arm}
              {p.status === "short_settled" && (
                <span
                  className="chip chip-warn integrity-chip"
                  title="The short expired ITM and delivered shares. They are covered next session together with the long's sale — the position is not closed while they are outstanding."
                >
                  awaiting disposal
                </span>
              )}
            </td>
            <td>{p.entrySession === "" ? "—" : p.entrySession}</td>
            <td>{p.quantity ?? "—"}</td>
            <td title="the diagonal's net debit, per share">{fmtPrice(p.netDebit === null ? null : -p.netDebit)}</td>
            <td>{fmtCash(p.entryCash)}</td>
            <td>
              <UnrealisedPnlCell
                gross={p.unrealisedGross}
                net={p.unrealisedNet}
                fees={p.feesToDate}
                detail={p.rollCount !== null && p.rollCount > 0 ? `${p.rollCount} roll(s) in fees` : undefined}
              />
            </td>
            <td>{strikeAt(p.longStrike, p.longExpiration, heldLong)}</td>
            <td>
              {strikeAt(p.shortStrike, p.shortExpiration)}
              {p.rollCount !== null && p.rollCount > 0 && (
                <sup title={`rolled ${String(p.rollCount)}×`}>+{p.rollCount}</sup>
              )}
            </td>
            <td>
              <TvCell tv={p.currentShortTv} threshold={heldLong ? null : params.tvCloseThreshold} />
            </td>
            <td>{fmtNum(p.currentSpot ?? p.entrySpot, 2)}</td>
            {!heldLong && <td>{fmtPct(p.entryWeeklyYieldPct === null ? null : p.entryWeeklyYieldPct * 100, 2)}</td>}
            <td>
              <EntrySpreadCell pct={p.entryMaxSpreadPct} abs={p.entryMaxSpreadAbs} netTv={p.entryNetTv} />
            </td>
            {!heldLong && (
              <td>{fmtPct(p.downsideProtectionPct === null ? null : p.downsideProtectionPct * 100, 1)}</td>
            )}
            <td>
              {p.exposedTicks > 0 ? (
                <span
                  className="chip chip-warn integrity-chip"
                  title={`${String(p.exposedTicks)} of ${String(p.markedTicks)} usable short marks sat under the assignment-exposure threshold. Telemetry only — it gates nothing.`}
                >
                  exposed {fmtPct(exposedShare, 0)}
                </span>
              ) : settlementStyle[p.symbol] === "cash" ? (
                <span className="muted" title="Cash-settled, European-exercise: no early-assignment risk exists, so this telemetry is exempt for it by design.">
                  n/a — cash-settled
                </span>
              ) : (
                <span className="muted">—</span>
              )}
            </td>
          </tr>
        );
      })}
    </>
  );
}

/**
 * Every open PMCC, one row each, in one table per lifecycle.
 *
 * A held-long position (the base trade since 2026-10-06) and a weekly control position are
 * different structures: the first holds a ~1-year long and is judged on its roll triggers, the
 * second re-buys a ~21-DTE long and exits on control's time-value threshold. One table drew
 * control's threshold and yield columns across shield rows, where they never apply. Weekly rows
 * remain only while control's last positions run off.
 *
 * The long and short expiries stay on their own strikes, as full dates, rather than becoming one
 * "expiry" column: a PMCC's two legs expire on DIFFERENT dates by construction.
 */
export function OpenTradesCard({
  data,
  updatedAt,
  symbol: filterSymbol = null,
}: {
  data: PmccPayload | undefined;
  updatedAt?: number;
  /** Show only this symbol; null shows every open trade. */
  symbol?: string | null;
}) {
  if (data === undefined) return null;
  const rows = data.openPositions
    .filter((p) => filterSymbol === null || p.symbol === filterSymbol)
    // Newest entry first; symbol and arm break ties within a session.
    .sort(
      (a, b) =>
        b.entrySession.localeCompare(a.entrySession) ||
        a.symbol.localeCompare(b.symbol) ||
        a.arm.localeCompare(b.arm),
    );
  const held = rows.filter((p) => p.lifecycle === "held_long");
  const weekly = rows.filter((p) => p.lifecycle !== "held_long");
  return (
    <Card title="open trades" collapseKey="pmcc-open-trades" updatedAt={updatedAt}>
      {rows.length === 0 && (
        <p className="muted">no open trades{filterSymbol === null ? "" : ` on ${filterSymbol}`}</p>
      )}
      {held.length > 0 && <OpenTable title="held long" rows={held} params={data.params} heldLong />}
      {weekly.length > 0 && (
        <OpenTable
          title="weekly — control, running off"
          note="Control retired on 2026-10-06: these finish under their own rules and nothing replaces them."
          rows={weekly}
          params={data.params}
          heldLong={false}
        />
      )}
      <div className="card-footer">
        <Link to="/pmcc/tracker">every position, week by week →</Link>
      </div>
    </Card>
  );
}

function OpenTable({
  title,
  note,
  rows,
  params,
  heldLong,
}: {
  title: string;
  note?: string;
  rows: PmccOpenPosition[];
  params: PmccPayload["params"];
  heldLong: boolean;
}) {
  return (
    <section className="pmcc-compare">
      <h3>{title}</h3>
      <div className="table-scroll">
        <table className="data-table num-from-4">
          <thead>
            <tr>
              <th>symbol</th>
              <th>arm</th>
              <th>opened</th>
              <th>qty</th>
              <th>price</th>
              <th>entry</th>
              <th>mark (net of costs to date)</th>
              <th>long</th>
              <th>short</th>
              <th>time value</th>
              <th>spot</th>
              {!heldLong && <th>weekly yield</th>}
              <th>entry spread</th>
              {!heldLong && <th>protection</th>}
              <th>assignment</th>
            </tr>
          </thead>
          <tbody>
            <PositionRows rows={rows} params={params} heldLong={heldLong} />
          </tbody>
        </table>
      </div>
      {note !== undefined && <p className="integrity-note">{note}</p>}
    </section>
  );
}

interface BookTotals {
  arm: string;
  positions: number;
  net: number | null;
  rolls: number | null;
  wins: number;
}

function totalsByBook(arms: PmccArmCell[]): BookTotals[] {
  const map = new Map<string, BookTotals>();
  for (const cell of arms) {
    const t = map.get(cell.arm) ?? { arm: cell.arm, positions: 0, net: null, rolls: null, wins: 0 };
    t.positions += cell.positions;
    if (cell.netPnl !== null) t.net = (t.net ?? 0) + cell.netPnl;
    if (cell.rolls !== null) t.rolls = (t.rolls ?? 0) + cell.rolls;
    if (cell.winRate !== null) t.wins += cell.winRate * cell.positions;
    map.set(cell.arm, t);
  }
  // Core arms first in their declared order, then anything else (advised arms) alphabetically.
  return [...map.values()].sort((a, b) => {
    const ia = CORE_BOOKS.indexOf(a.arm);
    const ib = CORE_BOOKS.indexOf(b.arm);
    if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    return a.arm.localeCompare(b.arm);
  });
}

/**
 * The arm comparison.
 *
 * The base trade since 2026-10-06 is the held-long pair, `shield` (rolls early) and `shield_hold`
 * (holds each short to Friday): same entries on the same days, so their difference is the early
 * roll. Control (retired that day) and the advisor's `advised:<experiment name>` arms remain as
 * history; the advised ones are called out separately because their admitted params can differ
 * position to position.
 */
/** Open positions' mark-to-market per (arm, symbol): the module's `open_mtm`, which the mirror test
 *  pins this sum to. Null while any position in the cell is unpriceable. */
function openMarkByArm(rows: PmccOpenPosition[]): Array<{ arm: string; symbol: string; positions: number; net: number | null }> {
  const cells = new Map<string, { arm: string; symbol: string; positions: number; net: number | null }>();
  for (const p of rows) {
    const key = `${p.arm}/${p.symbol}`;
    const cell = cells.get(key) ?? { arm: p.arm, symbol: p.symbol, positions: 0, net: 0 };
    cell.positions += 1;
    cell.net = cell.net === null || p.unrealisedNet === null ? null : Math.round((cell.net + p.unrealisedNet) * 100) / 100;
    cells.set(key, cell);
  }
  return [...cells.values()].sort((a, b) => a.arm.localeCompare(b.arm) || a.symbol.localeCompare(b.symbol));
}

export function BookComparison({
  data,
  updatedAt,
  symbol = null,
}: {
  data: PmccPayload | undefined;
  updatedAt?: number;
  /** Scope the totals to one symbol's closed cycles; null pools every symbol (TQQQ and XSP are
   *  measured as separate populations, so a symbol filter here is a real scoping choice, not
   *  cosmetic). */
  symbol?: string | null;
}) {
  const arms = (data?.arms ?? []).filter((b) => symbol === null || b.symbol === symbol);
  const totals = totalsByBook(arms);
  const open = openMarkByArm((data?.openPositions ?? []).filter((p) => symbol === null || p.symbol === symbol));
  const others = totals.filter((t) => isAdvised(t.arm));
  const maxAbs = Math.max(1, ...totals.map((t) => Math.abs(t.net ?? 0)));
  const hasClosed = arms.length > 0;

  return (
    <Card title="arm comparison" collapseKey="pmcc-arms" updatedAt={updatedAt}>
      {open.length > 0 && (
        <section className="pmcc-compare">
          <h3>open, marked to market</h3>
          <table className="data-table num-from-2">
            <thead>
              <tr>
                <th>arm</th>
                <th>symbol</th>
                <th>open</th>
                <th title="every leg at its latest usable mark or its close, shares held or covered, less every cost so far">
                  net to date
                </th>
              </tr>
            </thead>
            <tbody>
              {open.map((o) => (
                <tr key={`${o.arm}/${o.symbol}`}>
                  <td className="mono">{o.arm}</td>
                  <td>{o.symbol}</td>
                  <td>{o.positions}</td>
                  <td>
                    <PnlCell v={o.net} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="integrity-note">
            A held-long arm has no closed result until its long is sold, ~10 months after entry; until then this is its
            only number. A cell with an unpriceable position shows a dash rather than a partial sum.
          </p>
        </section>
      )}
      {!hasClosed ? (
        <p className="muted">
          no completed cycles yet — per-arm results fill in as positions close
          {data !== undefined && data.openCount > 0 && (
            <> ({data.openCount} open position{data.openCount === 1 ? "" : "s"} so far)</>
          )}
        </p>
      ) : (
        <>
          {others.length > 0 && (
            <section className="pmcc-compare">
              <h3>advised arms</h3>
              <p className="integrity-note">
                One synthetic arm per advisor experiment (advised:&lt;experiment name&gt;), each running its own
                admitted params beside the base arm it shadowed. History only: pmcc&apos;s advice was switched off
                on 2026-10-06 with control.
              </p>
              <ul className="integrity-plain-list">
                {others.map((t) => (
                  <li key={t.arm}>
                    <span className="mono">{t.arm}</span> · {t.positions} cycle{t.positions === 1 ? "" : "s"} · net{" "}
                    <PnlCell v={t.net} />
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="pmcc-compare">
            <h3>net by arm</h3>
            <table className="data-table num-from-1">
              <tbody>
                {totals.map((t) => (
                  <tr key={t.arm}>
                    <td>{t.arm}</td>
                    <td style={{ width: "50%" }}>
                      <SignedBar value={t.net ?? 0} maxAbs={maxAbs} compact />
                    </td>
                    <td>
                      <PnlCell v={t.net} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="integrity-note">
              Every figure here is net of the modeled fee and slippage stack — and is still an upper bound while
              early assignment sits unmodelled.
            </p>
          </section>
        </>
      )}
    </Card>
  );
}
