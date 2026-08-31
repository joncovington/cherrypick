import type { CurveBookCell, CurveFlipDivergence, CurveOpenPosition, CurvePayload, CurveRegimeRow } from "@console/shared";
import { Card, DataCard, PnlCell, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { UnrealisedPnlCell } from "../../components/UnrealisedPnlCell";
import { SignedBar } from "../../components/Charts";
import { fmtStrike } from "../../lib/optionFormat";

/** The three books whose identity the page knows. An `advised:*` twin rides along generically. */
const CORE_BOOKS = ["control", "noflip", "hook"];

function strikeAt(strike: number | null, expiration: string | null): string {
  if (strike === null) return "—";
  return `${fmtStrike(strike)}${expiration === null ? "" : ` @ ${expiration.slice(5)}`}`;
}

/**
 * Close cost against the entry credit -- the whole trade is this number decaying toward the
 * profit-take threshold. A null renders as an em-dash with its reason on the title, never as
 * $0.00 -- "no usable mark" and "already at zero cost" are different facts.
 */
function CloseCostCell({ cost, credit }: { cost: number | null; credit: number | null }) {
  if (cost === null) {
    return (
      <span className="muted" title="no usable mark recorded yet -- not the same as a zero cost to close">
        —
      </span>
    );
  }
  const pctOfCredit = credit !== null && credit > 0 ? (cost / credit) * 100 : null;
  return (
    <span>
      {fmtMoney(cost)}
      {pctOfCredit !== null && <span className="muted"> ({fmtPct(pctOfCredit, 0)} of credit)</span>}
    </span>
  );
}

function PositionRows({ rows }: { rows: CurveOpenPosition[] }) {
  return (
    <>
      {rows.map((p) => (
        <tr key={p.positionId}>
          <td>{p.symbol}</td>
          <td>{p.book}</td>
          <td title={p.entrySession}>{p.entrySession === "" ? "—" : p.entrySession.slice(5)}</td>
          <td title={p.expiration ?? undefined}>{p.expiration === null ? "—" : p.expiration.slice(5)}</td>
          <td>
            <UnrealisedPnlCell
              gross={p.unrealisedGross}
              net={p.unrealisedNet}
              fees={p.feesToDate}
              detail={p.currentCloseCost === null ? undefined : `costs ${fmtMoney(p.currentCloseCost)}/share to close`}
            />
          </td>
          <td>
            {strikeAt(p.shortStrike, p.expiration)}
            <span className="muted"> / </span>
            {strikeAt(p.longStrike, p.expiration)}
          </td>
          <td>{fmtNum(p.currentSpot ?? p.entrySpot, 2)}</td>
          <td>{fmtMoney(p.entryCredit)}</td>
          <td>{fmtPct(p.entryCreditPctOfWidth === null ? null : p.entryCreditPctOfWidth * 100, 1)}</td>
          <td>
            {p.entryRatio === null ? "—" : fmtNum(p.entryRatio, 3)}
            {p.entryRegime !== null && <span className="muted"> ({p.entryRegime})</span>}
            {p.entryHook && (
              <span className="chip chip-warn integrity-chip" title="entered on the two-day-confirmed hook signal">
                hook
              </span>
            )}
          </td>
          <td>
            {p.exposureTicks !== null && p.exposureTicks > 0 ? (
              <span
                className="chip chip-warn integrity-chip"
                title="ticks where the short's extrinsic sat under the assignment-exposure threshold. Telemetry only -- it gates nothing."
              >
                exposed {p.exposureTicks}
              </span>
            ) : (
              <span className="muted">—</span>
            )}
          </td>
        </tr>
      ))}
    </>
  );
}

/**
 * One card per symbol, listing only the books actually holding a position.
 *
 * VXX is the module's only underlying, so in practice this is one card -- but a book holding
 * nothing is signal, not absence: the hook book idling all week is the experiment working exactly
 * as designed (the pmcc keltner precedent, restated for curve's rarer entry).
 */
export function OpenTradesCard({ data, updatedAt }: { data: CurvePayload | undefined; updatedAt?: number }) {
  if (data === undefined) return null;
  const symbols = [...new Set(data.openPositions.map((p) => p.symbol))];
  if (symbols.length === 0) {
    return (
      <DataCard
        title="open trades"
        headers={["symbol", "book", "entry", "expiry", "P&L (net of costs to date)", "short/long", "spot", "credit", "credit % of width", "ratio/regime", "assignment"]}
        loading={false}
        rowCount={0}
        numFrom={5}
        empty="no open trades -- one position per book at ~30-45 DTE, so idle stretches are the ordinary state"
        updatedAt={updatedAt}
      >
        {null}
      </DataCard>
    );
  }
  // One table, not one card per symbol. curve trades a single underlying today, so a per-symbol
  // split was a card whose title repeated the only value the column could hold; symbol and expiry
  // moved onto the row, where they identify the trade rather than the container.
  const rows = [...data.openPositions].sort(
    (a, b) =>
      b.entrySession.localeCompare(a.entrySession) ||
      a.symbol.localeCompare(b.symbol) ||
      a.book.localeCompare(b.book),
  );
  return (
    <Card title="open trades" collapseKey="curve-open-trades" updatedAt={updatedAt}>
      <div className="table-scroll">
        <table className="data-table num-from-5">
          <thead>
            <tr>
              {["symbol", "book", "entry", "expiry", "P&L (net of costs to date)", "short/long", "spot", "credit", "credit % of width", "ratio/regime", "assignment"].map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            <PositionRows rows={rows} />
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/** Today's regime read: the module's second product, standing on its own beside any position. */
export function RegimeCard({ series, today, updatedAt }: { series: CurveRegimeRow[]; today: CurveRegimeRow | undefined; updatedAt?: number }) {
  return (
    <Card title="VIX/VIX3M regime -- the module's second product" collapseKey="curve-regime" updatedAt={updatedAt} className="view-fade">
      <p className="integrity-note">
        Written every session, traded or not -- the series' value is its continuity, never only what fed a trade.
      </p>
      {today === undefined ? (
        <p className="muted">no regime row for the current session yet</p>
      ) : !today.usable ? (
        <p className="integrity-warn">
          today's reading is unusable{today.refusal !== null && <> ({today.refusal})</>} -- a stale or missing quote
          refuses rather than freezing the last value forward
        </p>
      ) : (
        <p>
          ratio <strong>{fmtNum(today.ratio, 3)}</strong> ({today.regime}) -- VIX {fmtNum(today.vix, 2)} / VIX3M{" "}
          {fmtNum(today.vix3m, 2)}
          {today.hook === true && (
            <span className="chip chip-warn integrity-chip" title="the two-day-confirmed deep-backwardation hook signal">
              hook
            </span>
          )}
        </p>
      )}
      {series.length > 1 && (
        <div className="table-scroll">
          <table className="data-table num-from-1">
            <thead>
              <tr>
                <th>date</th>
                <th>ratio</th>
                <th>regime</th>
                <th>hook</th>
                <th>usable</th>
              </tr>
            </thead>
            <tbody>
              {series
                .slice()
                .reverse()
                .slice(0, 20)
                .map((r) => (
                  <tr key={r.tradeDate}>
                    <td>{r.tradeDate}</td>
                    <td>{fmtNum(r.ratio, 3)}</td>
                    <td>{r.regime ?? "—"}</td>
                    <td>{r.hook === true ? "hook" : ""}</td>
                    <td>{r.usable ? "" : <span className="chip chip-warn integrity-chip">{r.refusal ?? "unusable"}</span>}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

interface BookTotals {
  book: string;
  positions: number;
  net: number | null;
  wins: number;
}

function totalsByBook(books: CurveBookCell[]): BookTotals[] {
  const map = new Map<string, BookTotals>();
  for (const cell of books) {
    const t = map.get(cell.book) ?? { book: cell.book, positions: 0, net: null, wins: 0 };
    t.positions += cell.positions;
    if (cell.netPnl !== null) t.net = (t.net ?? 0) + cell.netPnl;
    if (cell.winRate !== null) t.wins += cell.winRate * cell.positions;
    map.set(cell.book, t);
  }
  return [...map.values()].sort((a, b) => {
    const ia = CORE_BOOKS.indexOf(a.book);
    const ib = CORE_BOOKS.indexOf(b.book);
    if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    return a.book.localeCompare(b.book);
  });
}

/**
 * The book comparison, split the way the module's own honesty rule requires: control/noflip are
 * exactly paired (same entry, same tick), but the FAIR sample for what the flip rule did is
 * `flip_divergence_count`, not the raw trade count -- until a flip fires the two books are
 * byte-identical by construction. hook gets its own section because its variable IS the entry tick.
 */
export function BookComparison({
  data,
  flipDivergence,
  updatedAt,
}: {
  data: CurvePayload | undefined;
  flipDivergence: CurveFlipDivergence | undefined;
  updatedAt?: number;
}) {
  const books = data?.books ?? [];
  const totals = totalsByBook(books);
  const symbols = [...new Set(books.map((b) => b.symbol))].sort();
  const cell = (book: string, symbol: string): CurveBookCell | undefined =>
    books.find((b) => b.book === book && b.symbol === symbol);
  const hook = totals.find((t) => t.book === "hook");
  const others = totals.filter((t) => !CORE_BOOKS.includes(t.book));
  const maxAbs = Math.max(1, ...totals.map((t) => Math.abs(t.net ?? 0)));
  const hasClosed = books.length > 0;

  return (
    <Card title="book comparison" collapseKey="curve-books" updatedAt={updatedAt}>
      {!hasClosed ? (
        <p className="muted">
          no completed cycles yet -- per-book results fill in as positions close
          {data !== undefined && data.openCount > 0 && (
            <> ({data.openCount} open position{data.openCount === 1 ? "" : "s"} so far)</>
          )}
        </p>
      ) : (
        <>
          <section className="pmcc-compare">
            <h3>control vs noflip -- the effective sample is flip_divergence, not trade count</h3>
            <p className="integrity-note">
              Both books enter from the SAME plan on the SAME tick. Until a flip actually fires they are
              byte-identical by construction, so the noflip comparison's real sample is{" "}
              <strong>{flipDivergence?.flipDivergenceCount ?? 0}</strong> position
              {(flipDivergence?.flipDivergenceCount ?? 0) === 1 ? "" : "s"} where control's flip fired while noflip
              held -- against {flipDivergence?.controlFlipExits ?? 0} control flip exits recorded in total. A season
              of pure contango with zero flips proves nothing about the flip rule.
            </p>
            <div className="table-scroll">
              <table className="data-table num-from-1">
                <thead>
                  <tr>
                    <th>symbol</th>
                    <th>control net</th>
                    <th>noflip net</th>
                    <th>delta</th>
                    <th>control cycles</th>
                    <th>noflip cycles</th>
                  </tr>
                </thead>
                <tbody>
                  {symbols.map((symbol) => {
                    const c = cell("control", symbol);
                    const n = cell("noflip", symbol);
                    const delta =
                      c?.netPnl === undefined || c.netPnl === null || n?.netPnl === undefined || n.netPnl === null
                        ? null
                        : n.netPnl - c.netPnl;
                    return (
                      <tr key={symbol}>
                        <td>{symbol}</td>
                        <td>
                          <PnlCell v={c?.netPnl ?? null} />
                        </td>
                        <td>
                          <PnlCell v={n?.netPnl ?? null} />
                        </td>
                        <td>
                          <PnlCell v={delta} />
                        </td>
                        <td>{c?.positions ?? 0}</td>
                        <td>{n?.positions ?? 0}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <section className="pmcc-compare">
            <h3>hook -- not row-comparable, and expected to be nearly always idle</h3>
            <p className="integrity-note">
              Its variable IS the entry tick (the two-day-confirmed deep-backwardation spike), so its fill set
              differs from control's by construction. Idleness here is the honest state, not a failure.
            </p>
            {hook === undefined || hook.positions === 0 ? (
              <p className="muted">no completed hook cycles -- the rare-event gate has not admitted an entry that closed yet</p>
            ) : (
              <p>
                {hook.positions} cycle{hook.positions === 1 ? "" : "s"} · net <PnlCell v={hook.net} /> · win rate{" "}
                {fmtPct(hook.positions > 0 ? (hook.wins / hook.positions) * 100 : null, 0)}
              </p>
            )}
          </section>

          {others.length > 0 && (
            <section className="pmcc-compare">
              <h3>advised books</h3>
              <p className="integrity-note">
                The advisor's synthetic twin runs its admitted params beside a base book. Excluded from the pairing
                above -- its entries are its own.
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
              Every figure here is net of the modeled fee and slippage stack -- and is still an upper bound while
              early assignment sits unmodelled.
            </p>
          </section>
        </>
      )}
    </Card>
  );
}
