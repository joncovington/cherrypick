import type { BwbBookCell, BwbFireCount, BwbOpenPosition, BwbPayload } from "@console/shared";
import { Card, DataCard, PnlCell, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { SignedBar } from "../../components/Charts";
import { fmtStrike } from "../../lib/optionFormat";

const CORE_BOOKS = ["control", "delta", "bounce", "flip"];

function strikeSet(near: number | null, body: number | null, far: number | null): string {
  if (near === null && body === null && far === null) return "—";
  return `${fmtStrike(near)} / ${fmtStrike(body)}x2 / ${fmtStrike(far)}`;
}

/**
 * Close cost against the entry credit -- never $0.00 for an unmarked position, "no usable mark" and
 * "already at zero cost" are different facts.
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

function TriggerCell({ p }: { p: BwbOpenPosition }) {
  if (p.addonFiredAt !== null) {
    return (
      <span className="chip chip-warn integrity-chip" title={`add-on fired ${p.addonFiredAt}`}>
        fired {p.addonCredit !== null && <>({fmtMoney(p.addonCredit)})</>}
      </span>
    );
  }
  if (p.armedAt !== null) {
    return (
      <span className="chip integrity-chip" title={`armed ${p.armedAt}`}>
        armed
      </span>
    );
  }
  return <span className="muted">idle</span>;
}

function PositionRows({ rows }: { rows: BwbOpenPosition[] }) {
  return (
    <>
      {rows.map((p) => (
        <tr key={p.positionId}>
          <td>{p.book}</td>
          <td>{strikeSet(p.nearStrike, p.bodyStrike, p.farStrike)}</td>
          <td>
            <CloseCostCell cost={p.currentCloseCost} credit={p.entryCredit} />
          </td>
          <td>{fmtNum(p.currentSpot ?? p.entrySpot, 2)}</td>
          <td>{fmtMoney(p.entryCredit)}</td>
          <td>{p.peakAbsDelta === null ? "—" : fmtNum(p.peakAbsDelta, 3)}</td>
          <td>{p.belowFlipSeen ? <span className="chip chip-warn integrity-chip">below flip</span> : <span className="muted">—</span>}</td>
          <td>
            <TriggerCell p={p} />
          </td>
        </tr>
      ))}
    </>
  );
}

/** One card per symbol, listing only the books actually holding a position. */
export function SymbolCards({ data, updatedAt }: { data: BwbPayload | undefined; updatedAt?: number }) {
  if (data === undefined) return null;
  const symbols = [...new Set(data.openPositions.map((p) => p.symbol))];
  const headers = ["book", "near/body x2/far", "close cost", "spot", "credit", "peak |delta|", "below flip", "add-on"];
  if (symbols.length === 0) {
    return (
      <DataCard
        title="open positions"
        headers={headers}
        loading={false}
        rowCount={0}
        numFrom={2}
        empty="no open positions -- the daily ladder accumulates one BWB per book per session"
        updatedAt={updatedAt}
      >
        {null}
      </DataCard>
    );
  }
  return (
    <>
      {symbols.map((symbol) => {
        const rows = data.openPositions.filter((p) => p.symbol === symbol);
        return (
          <Card key={symbol} title={symbol} collapseKey={`bwb-symbol-${symbol}`} updatedAt={updatedAt}>
            <div className="table-scroll">
              <table className="data-table num-from-2">
                <thead>
                  <tr>
                    {headers.map((h) => (
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
      })}
    </>
  );
}

/** Per-book add-on fire counts -- the plan's own honesty rule: the effective sample per arm is the
 * FIRE count, not the trade count. */
export function FireCountsCard({ counts, correlationCaveat, updatedAt }: { counts: BwbFireCount[]; correlationCaveat: string; updatedAt?: number }) {
  const arms = counts.filter((c) => c.book !== "control");
  return (
    <Card title="add-on fire counts -- the effective sample per arm" collapseKey="bwb-fires" updatedAt={updatedAt}>
      {arms.length === 0 ? (
        <p className="muted">no positions recorded yet</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table num-from-1">
            <thead>
              <tr>
                <th>book</th>
                <th>positions</th>
                <th>fired</th>
                <th>fire rate</th>
              </tr>
            </thead>
            <tbody>
              {arms.map((c) => (
                <tr key={c.book}>
                  <td>{c.book}</td>
                  <td>{c.positions}</td>
                  <td>{c.fired}</td>
                  <td>{fmtPct(c.fireRate === null ? null : c.fireRate * 100, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="integrity-note">{correlationCaveat}</p>
    </Card>
  );
}

interface BookTotals {
  book: string;
  positions: number;
  net: number | null;
  wins: number;
}

function totalsByBook(books: BwbBookCell[]): BookTotals[] {
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

/** Net-by-book, over CLOSED positions -- `analytics.headline()`. */
export function BookComparison({ data, updatedAt }: { data: BwbPayload | undefined; updatedAt?: number }) {
  const books = data?.books ?? [];
  const totals = totalsByBook(books);
  const maxAbs = Math.max(1, ...totals.map((t) => Math.abs(t.net ?? 0)));
  const hasClosed = books.length > 0;

  return (
    <Card title="net by book" collapseKey="bwb-books" updatedAt={updatedAt}>
      {!hasClosed ? (
        <p className="muted">
          no completed positions yet -- results fill in as the daily ladder settles at expiry
          {data !== undefined && data.openCount > 0 && (
            <> ({data.openCount} open position{data.openCount === 1 ? "" : "s"} so far)</>
          )}
        </p>
      ) : (
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
      )}
      <p className="integrity-note">
        Until an arm's add-on actually fires, its positions are byte-identical to control's -- an expected
        collision, not a defect. Read fires beside this table, not instead of it.
      </p>
    </Card>
  );
}
