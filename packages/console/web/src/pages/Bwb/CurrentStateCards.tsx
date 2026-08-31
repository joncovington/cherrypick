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
/**
 * Mark-to-market P&L, net of costs incurred so far.
 *
 * This replaced a "close cost (% of credit)" cell on 2026-08-31. That percentage was measured
 * against the ENTRY credit alone, so a position whose add-on had fired showed a wildly worse
 * number than its identical siblings purely because the add-on's credit was missing from the
 * denominator -- -1000% against -333% on the same strikes, same spot, same everything. P&L
 * collapses that to the truth: they are the same trade and they are worth the same.
 *
 * The close cost is kept on the title, since "what would it take to get out right now" is a
 * different and still useful question from "what is it worth".
 */
function UnrealisedPnlCell({ p }: { p: BwbOpenPosition }) {
  if (p.unrealisedNet === null) {
    return (
      <span className="muted" title="no usable mark recorded yet -- not the same as a zero P&L">
        —
      </span>
    );
  }
  const cost = p.currentCloseCost === null ? "" : ` · costs ${fmtMoney(p.currentCloseCost)}/share to close`;
  const fees = p.feesToDate === null ? "" : ` · ${fmtMoney(p.feesToDate)} fees to date`;
  return (
    <span
      className={p.unrealisedNet >= 0 ? "pnl-pos" : "pnl-neg"}
      title={`gross ${p.unrealisedGross === null ? "—" : fmtMoney(p.unrealisedGross)}${fees}${cost}`}
    >
      {fmtMoney(p.unrealisedNet)}
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
          <td>{p.symbol}</td>
          <td>{p.book}</td>
          {/* MM-DD, the pmcc precedent — every row here is near-dated, so the year is noise.
              The full date rides the title so it is still recoverable. */}
          <td title={p.entrySession}>{p.entrySession === "" ? "—" : p.entrySession.slice(5)}</td>
          <td title={p.expiration ?? undefined}>{p.expiration === null ? "—" : p.expiration.slice(5)}</td>
          <td>{strikeSet(p.nearStrike, p.bodyStrike, p.farStrike)}</td>
          <td>
            <UnrealisedPnlCell p={p} />
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

/** Every open BWB, one row each — symbol and expiry identify the trade, book identifies the arm. */
export function OpenTradesCard({ data, updatedAt }: { data: BwbPayload | undefined; updatedAt?: number }) {
  if (data === undefined) return null;
  const empty = data.openPositions.length === 0;
  const headers = ["symbol", "book", "entry", "expiry", "near/body x2/far", "P&L (net of costs to date)", "spot", "credit", "peak |delta|", "below flip", "add-on"];
  if (empty) {
    return (
      <DataCard
        title="open trades"
        headers={headers}
        loading={false}
        rowCount={0}
        numFrom={5}
        empty="no open trades -- the daily ladder accumulates one BWB per book per session"
        updatedAt={updatedAt}
      >
        {null}
      </DataCard>
    );
  }
  // One table, not one card per symbol. A card titled with a bare ticker said what the rows were
  // ABOUT but not what they WERE, and it split a book that is meant to be read as a whole -- the
  // ladder runs one BWB per book per session, so the interesting comparison is across books, which
  // a per-symbol split puts in separate cards the moment a second symbol exists. Symbol and expiry
  // moved onto the row instead, where they identify the trade rather than the container.
  // Newest entry first: the ladder adds one BWB per book per session, so the top of this table is
  // what today put on. Symbol and book only break ties within a session.
  const rows = [...data.openPositions].sort(
    (a, b) =>
      b.entrySession.localeCompare(a.entrySession) ||
      a.symbol.localeCompare(b.symbol) ||
      a.book.localeCompare(b.book),
  );
  return (
    <Card title="open trades" collapseKey="bwb-open-trades" updatedAt={updatedAt}>
      <div className="table-scroll">
        <table className="data-table num-from-5">
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
