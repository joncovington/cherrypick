import type { PmccBookCell, PmccOpenPosition, PmccPayload } from "@console/shared";
import { Card, PnlCell, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { SignedBar } from "../../components/Charts";
import { fmtStrike } from "../../lib/optionFormat";
import { EntrySpreadCell } from "./EntrySpread";

/**
 * The one book whose identity the page knows since the 2026-08-23 redesign. Anything else (the
 * `advised:control` synthetic twin) rides along generically.
 */
const CORE_BOOKS = ["control"];

function strikeAt(strike: number | null, expiration: string | null): string {
  if (strike === null) return "—";
  return `${fmtStrike(strike)}${expiration === null ? "" : ` @ ${expiration.slice(5)}`}`;
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

function PositionRows({ rows, params }: { rows: PmccOpenPosition[]; params: PmccPayload["params"] }) {
  const settlementStyle = params.settlementStyle;
  return (
    <>
      {rows.map((p) => {
        const exposedShare = p.markedTicks > 0 ? (p.exposedTicks / p.markedTicks) * 100 : null;
        return (
          <tr key={p.positionId}>
            <td>
              {p.book}
              {p.status === "short_settled" && (
                <span
                  className="chip chip-warn integrity-chip"
                  title="The short expired ITM and delivered shares. They are covered next session together with the long's sale — the position is not closed while they are outstanding."
                >
                  awaiting disposal
                </span>
              )}
            </td>
            <td>{strikeAt(p.longStrike, p.longExpiration)}</td>
            <td>
              {strikeAt(p.shortStrike, p.shortExpiration)}
              {p.rollCount !== null && p.rollCount > 0 && (
                <sup title={`rolled ${String(p.rollCount)}×`}>+{p.rollCount}</sup>
              )}
            </td>
            <td>
              <TvCell tv={p.currentShortTv} threshold={params.tvCloseThreshold} />
            </td>
            <td>{fmtNum(p.currentSpot ?? p.entrySpot, 2)}</td>
            <td>{fmtPct(p.entryWeeklyYieldPct === null ? null : p.entryWeeklyYieldPct * 100, 2)}</td>
            <td>
              <EntrySpreadCell pct={p.entryMaxSpreadPct} abs={p.entryMaxSpreadAbs} netTv={p.entryNetTv} />
            </td>
            <td>{fmtPct(p.downsideProtectionPct === null ? null : p.downsideProtectionPct * 100, 1)}</td>
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
 * One card per symbol, listing only the books actually holding a position.
 *
 * Since the 2026-08-23 redesign the module trades two symbols (TQQQ, physical-settlement; XSP,
 * cash-settled, added the same day) in one book (`control`) each, as separate populations, plus
 * its synthetic `advised:control` twin when the advisor is running an experiment — so a symbol with
 * no open row simply says so, with no gate sub-row to explain (there is no more entry gate to name;
 * mechanical entry either finds the slot free or it doesn't).
 */
export function SymbolCards({
  data,
  updatedAt,
  symbol: filterSymbol = null,
}: {
  data: PmccPayload | undefined;
  updatedAt?: number;
  /** Show only this symbol's card; null shows every symbol. */
  symbol?: string | null;
}) {
  const allSymbols = data?.params.symbols ?? [];
  const symbols = filterSymbol === null ? allSymbols : allSymbols.filter((s) => s === filterSymbol);
  if (data === undefined) return null;
  return (
    <>
      {symbols.map((symbol) => {
        const rows = data.openPositions.filter((p) => p.symbol === symbol);
        return (
          <Card key={symbol} title={symbol} collapseKey={`pmcc-symbol-${symbol}`} updatedAt={updatedAt}>
            <div className="table-scroll">
              <table className="data-table num-from-3">
                <thead>
                  <tr>
                    <th>book</th>
                    <th>long</th>
                    <th>short</th>
                    <th>time value</th>
                    <th>spot</th>
                    <th>weekly yield</th>
                    <th>entry spread</th>
                    <th>protection</th>
                    <th>assignment</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 ? (
                    <tr>
                      <td colSpan={9} className="muted">
                        no open position on {symbol}
                      </td>
                    </tr>
                  ) : (
                    <PositionRows rows={rows} params={data.params} />
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        );
      })}
    </>
  );
}

interface BookTotals {
  book: string;
  positions: number;
  net: number | null;
  rolls: number | null;
  wins: number;
}

function totalsByBook(books: PmccBookCell[]): BookTotals[] {
  const map = new Map<string, BookTotals>();
  for (const cell of books) {
    const t = map.get(cell.book) ?? { book: cell.book, positions: 0, net: null, rolls: null, wins: 0 };
    t.positions += cell.positions;
    if (cell.netPnl !== null) t.net = (t.net ?? 0) + cell.netPnl;
    if (cell.rolls !== null) t.rolls = (t.rolls ?? 0) + cell.rolls;
    if (cell.winRate !== null) t.wins += cell.winRate * cell.positions;
    map.set(cell.book, t);
  }
  // Core books first in their declared order, then anything else (advised twins) alphabetically.
  return [...map.values()].sort((a, b) => {
    const ia = CORE_BOOKS.indexOf(a.book);
    const ib = CORE_BOOKS.indexOf(b.book);
    if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    return a.book.localeCompare(b.book);
  });
}

/**
 * The book comparison.
 *
 * Since the 2026-08-23 redesign there is one book (`control`) plus the advisor's optional synthetic
 * `advised:control` twin — no more multi-book fill pairing to reason about (the old control/keltner/
 * roll grid, and the caveat that keltner and roll could not be read across the same seam, is
 * retired). Every `control` cycle is directly comparable to every other `control` cycle; the advised
 * twin is called out separately because its admitted params can differ position to position.
 */
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
  const books = (data?.books ?? []).filter((b) => symbol === null || b.symbol === symbol);
  const totals = totalsByBook(books);
  const others = totals.filter((t) => !CORE_BOOKS.includes(t.book));
  const maxAbs = Math.max(1, ...totals.map((t) => Math.abs(t.net ?? 0)));
  const hasClosed = books.length > 0;

  return (
    <Card title="book comparison" collapseKey="pmcc-books" updatedAt={updatedAt}>
      {!hasClosed ? (
        <p className="muted">
          no completed cycles yet — per-book results fill in as positions close
          {data !== undefined && data.openCount > 0 && (
            <> ({data.openCount} open position{data.openCount === 1 ? "" : "s"} so far)</>
          )}
        </p>
      ) : (
        <>
          {others.length > 0 && (
            <section className="pmcc-compare">
              <h3>advised books</h3>
              <p className="integrity-note">
                The advisor's synthetic twin runs its admitted params beside a base book. It is excluded from the
                pairing above — its entries are its own.
              </p>
              <ul className="integrity-plain-list">
                {others.map((t) => (
                  <li key={t.book}>
                    <span className="mono">{t.book}</span> · {t.positions} cycle{t.positions === 1 ? "" : "s"} · net{" "}
                    <PnlCell v={t.net} />
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="pmcc-compare">
            <h3>net by book</h3>
            <table className="data-table num-from-1">
              <tbody>
                {totals.map((t) => (
                  <tr key={t.book}>
                    <td>{t.book}</td>
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
