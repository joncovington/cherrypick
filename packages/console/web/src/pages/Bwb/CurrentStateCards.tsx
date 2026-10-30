import type { BwbFireCount, BwbOpenPosition, BwbPayload } from "@console/shared";
import { Card, DataCard, SortTh, fmtMoney, fmtNum, fmtPct, sortRows, useSort } from "../../components/DataTable";
import { UnrealisedPnlCell } from "../../components/UnrealisedPnlCell";
import { fmtStrike } from "../../lib/optionFormat";
import { fmtCash, fmtPrice } from "../../lib/format";
import { Fragment, useState } from "react";
import { ArmSelect } from "../../components/ArmSelect";

function strikeSet(near: number | null, body: number | null, far: number | null): string {
  if (near === null && body === null && far === null) return "—";
  return `${fmtStrike(near)} / ${fmtStrike(body)}x2 / ${fmtStrike(far)}`;
}

/**
 * Close cost against the entry credit -- never $0.00 for an unmarked position, "no usable mark" and
 * "already at zero cost" are different facts.
 */

/**
 * The add-on's own strikes: a put credit spread, short the higher and long the lower.
 *
 * Shown beside the fly's `near/body x2/far` rather than folded into it, because a fired position is
 * a 1-3-2 and the two halves were priced at different moments -- the fly at entry, the add-on when
 * the trigger fired. Reading them as one five-strike structure would suggest they were chosen
 * together, which is the one thing the four arms exist to disagree about.
 */
function addonStrikes(short: number | null, long: number | null): string | null {
  if (short === null && long === null) return null;
  return `${fmtStrike(short)}/${fmtStrike(long)}`;
}

function TriggerCell({ p }: { p: BwbOpenPosition }) {
  if (p.addonFiredAt !== null) {
    const strikes = addonStrikes(p.addonShortStrike, p.addonLongStrike);
    return (
      <span title={`add-on fired ${p.addonFiredAt}`}>
        <span className="chip chip-warn integrity-chip">fired</span>{" "}
        {/* The strikes are the point of the cell once it has fired -- "fired" alone says the shape
            changed without saying what it changed to. A fired row with no strikes recorded shows the
            dash rather than an invented pair. */}
        {strikes ?? <span className="muted">—</span>}
        {p.addonCredit !== null && <span className="muted"> ({fmtMoney(p.addonCredit)})</span>}
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

/**
 * The open BWBs (2026-10-30, docs/console-review-plan.md batch 3): the row identifies the trade and
 * keeps its money together -- the fly's credit, the add-on's, the mark net of costs to date -- and
 * the trigger. The quantity, the per-share price, the spot, the peak delta and the flip flag are in
 * the row's expanded line.
 */
function PositionRows({ rows }: { rows: BwbOpenPosition[] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      {rows.map((p) => {
        const isOpen = open === p.positionId;
        return (
          <Fragment key={p.positionId}>
            <tr>
              <td>
                <button type="button" className="btn btn-quiet" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : p.positionId)}>
                  {isOpen ? "▾" : "▸"} {p.symbol}
                </button>
                {p.belowFlipSeen ? <span className="chip chip-warn integrity-chip">below flip</span> : null}
              </td>
              <td>{p.arm}</td>
              <td>
                {p.entrySession === "" ? "—" : p.entrySession} <span className="muted">→ {p.expiration ?? "—"}</span>
              </td>
              <td>{strikeSet(p.nearStrike, p.bodyStrike, p.farStrike)}</td>
              <td title="the fly's opening credit, whole position">{fmtCash(p.entryCash)}</td>
              <td title="the add-on credit spread's credit, whole position, once its trigger fired">
                {p.addOnCash === null ? <span className="muted">—</span> : fmtCash(p.addOnCash)}
              </td>
              <td>
                <UnrealisedPnlCell
                  gross={p.unrealisedGross}
                  net={p.unrealisedNet}
                  fees={p.feesToDate}
                  detail={p.currentCloseCost === null ? undefined : `costs ${fmtMoney(p.currentCloseCost)}/share to close`}
                />
              </td>
              <td>
                <TriggerCell p={p} />
              </td>
            </tr>
            {isOpen && (
              <tr className="row-detail">
                <td colSpan={HEADERS.length}>
                  <table className="data-table secondary-cells" aria-label={`${p.symbol} ${p.arm} position detail`}>
                    <thead>
                      <tr>
                        <th>qty</th>
                        <th title="the fly's own net credit, per share">price</th>
                        <th>spot</th>
                        <th>peak |delta|</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>{p.quantity ?? "—"}</td>
                        <td>{fmtPrice(p.entryCredit)}</td>
                        <td>{fmtNum(p.currentSpot ?? p.entrySpot, 2)}</td>
                        <td>{p.peakAbsDelta === null ? "—" : fmtNum(p.peakAbsDelta, 3)}</td>
                      </tr>
                    </tbody>
                  </table>
                </td>
              </tr>
            )}
          </Fragment>
        );
      })}
    </>
  );
}

const HEADERS = ["symbol", "arm", "opened → expiry", "near/body x2/far", "entry", "add-on $", "mark (net of costs to date)", "add-on (short/long)"];

/** Every open BWB, one row each — symbol and expiry identify the trade, arm identifies the arm. */
export function OpenTradesCard({ data, updatedAt }: { data: BwbPayload | undefined; updatedAt?: number }) {
  // The arm filter: every arm with an open trade, retired ones behind the shared toggle.
  const [arm, setArm] = useState<string | null>(null);
  const sort = useSort();
  if (data === undefined) return null;
  const empty = data.openPositions.length === 0;
  const headers = HEADERS;
  if (empty) {
    return (
      <DataCard
        title="open trades"
        headers={headers}
        loading={false}
        rowCount={0}
        numFrom={4}
        empty="no open trades -- the daily ladder accumulates one BWB per arm per session"
        updatedAt={updatedAt}
      >
        {null}
      </DataCard>
    );
  }
  // One table, not one card per symbol. A card titled with a bare ticker said what the rows were
  // ABOUT but not what they WERE, and it split a arm that is meant to be read as a whole -- the
  // ladder runs one BWB per arm per session, so the interesting comparison is across arms, which
  // a per-symbol split puts in separate cards the moment a second symbol exists. Symbol and expiry
  // moved onto the row instead, where they identify the trade rather than the container.
  // Newest entry first: the ladder adds one BWB per arm per session, so the top of this table is
  // what today put on. Symbol and arm only break ties within a session.
  const arms = [...new Set(data.openPositions.map((p) => p.arm))].sort();
  const ordered = [...data.openPositions]
    .filter((p) => arm === null || p.arm === arm)
    .sort(
      (a, b) =>
        b.entrySession.localeCompare(a.entrySession) ||
        a.symbol.localeCompare(b.symbol) ||
        a.arm.localeCompare(b.arm),
    );
  // A header click re-orders the whole filtered set; the third click is back to newest first. The
  // strikes, the flip flag and the trigger cell are composite, so they stay fixed headers.
  const rows = sortRows(ordered, sort, {
    symbol: (p) => p.symbol,
    arm: (p) => p.arm,
    opened: (p) => (p.entrySession === "" ? null : p.entrySession),
    expiry: (p) => p.expiration,
    qty: (p) => p.quantity,
    price: (p) => p.entryCredit,
    entry: (p) => p.entryCash,
    addon: (p) => p.addOnCash,
    mark: (p) => p.unrealisedNet,
    spot: (p) => p.currentSpot ?? p.entrySpot,
    delta: (p) => p.peakAbsDelta,
  });
  const sortKeys: Record<string, string> = {
    symbol: "symbol",
    arm: "arm",
    "opened → expiry": "opened",
    entry: "entry",
    "add-on $": "addon",
    "mark (net of costs to date)": "mark",
  };
  return (
    <Card
      title={arm === null ? "open trades" : `open trades · ${arm} (${rows.length})`}
      collapseKey="bwb-open-trades"
      updatedAt={updatedAt}
      controls={<ArmSelect module="bwb" value={arm} options={arms} onChange={setArm} />}
    >
      <div className="table-scroll">
        <table className="data-table num-from-4">
          <thead>
            <tr>
              {headers.map((h) => {
                const k = sortKeys[h];
                return k === undefined ? <th key={h}>{h}</th> : <SortTh key={h} label={h} k={k} sort={sort} />;
              })}
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

/** Per-arm add-on fire counts -- the plan's own honesty rule: the effective sample per arm is the
 * FIRE count, not the trade count. */
export function FireCountsCard({ counts, correlationCaveat, updatedAt }: { counts: BwbFireCount[]; correlationCaveat: string; updatedAt?: number }) {
  const arms = counts.filter((c) => c.arm !== "control");
  return (
    <Card title="add-on fire counts -- the effective sample per arm" collapseKey="bwb-fires" updatedAt={updatedAt}>
      {arms.length === 0 ? (
        <p className="muted">no positions recorded yet</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table num-from-1">
            <thead>
              <tr>
                <th>arm</th>
                <th>positions</th>
                <th>fired</th>
                <th>fire rate</th>
              </tr>
            </thead>
            <tbody>
              {arms.map((c) => (
                <tr key={c.arm}>
                  <td>{c.arm}</td>
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
