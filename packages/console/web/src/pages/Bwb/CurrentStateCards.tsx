import type { BwbFireCount, BwbOpenPosition, BwbPayload } from "@console/shared";
import { Card, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import type { SortOption } from "../../components/FilterSort";
import { fmtStrike } from "../../lib/optionFormat";
import { fmtCash, fmtPrice } from "../../lib/format";
import { useState } from "react";
import { ArmSelect } from "../../components/ArmSelect";
import { TradeListCard } from "../Live/TradeListCard";
import type { TradeCardLeg, TradeCardModel } from "../Live/TradeCard";

export const BWB_OPEN_SORTS: SortOption<BwbOpenPosition>[] = [
  { id: "time", label: "entry session", hint: "newest first", key: (p) => (p.entrySession === "" ? null : -Date.parse(p.entrySession)) },
  { id: "expiry", label: "expiry", hint: "soonest first", key: (p) => p.expiration },
  { id: "size", label: "size", hint: "largest worst case first", key: (p) => (p.entryMaxLoss === null ? null : -p.entryMaxLoss * (p.quantity ?? 1)) },
  { id: "arm", label: "arm", hint: "A to Z", key: (p) => p.arm },
];

const OPEN_HELP: CardHelpContent = {
  title: "Open trades",
  body: (
    <>
      <HelpSection title="A trade">
        <p>
          One BWB, with the strikes as the order that opened it: the fly (near, the body twice, far) and, once its trigger has fired,
          the add-on put credit spread. The headline cash is the fly&apos;s opening credit for the whole position. The mark is net of
          costs to date; the settlement fee is not in it yet.
        </p>
      </HelpSection>
      <HelpSection title="Add-on">
        <p>
          The add-on&apos;s strikes are shown apart from the fly&apos;s, because the two halves were priced at different moments. Reading
          them as one five-strike structure would suggest they were chosen together, which is the one thing the arms differ on.
        </p>
      </HelpSection>
      <HelpSection title="Sort">
        <p>Newest entry first by default: the ladder adds one BWB per arm per session, so the top is what today put on.</p>
      </HelpSection>
    </>
  ),
};

/** An open BWB as a trade card: the fly's legs, then the add-on's when it has fired. */
export function bwbPositionModel(p: BwbOpenPosition): TradeCardModel {
  const q = p.quantity ?? 1;
  const spot = p.currentSpot ?? p.entrySpot;
  const fired = p.addonFiredAt !== null;
  const legs: TradeCardLeg[] = [
    { buy: true, text: `${String(q)} ${fmtStrike(p.nearStrike)}P`, quote: null },
    { buy: false, text: `${String(2 * q)} ${fmtStrike(p.bodyStrike)}P`, quote: null },
    { buy: true, text: `${String(q)} ${fmtStrike(p.farStrike)}P`, quote: null },
  ];
  if (fired) {
    legs.push(
      { buy: false, text: `${String(q)} ${fmtStrike(p.addonShortStrike)}P add-on`, quote: null },
      { buy: true, text: `${String(q)} ${fmtStrike(p.addonLongStrike)}P add-on`, quote: null },
    );
  }
  return {
    key: p.positionId,
    symbol: p.symbol,
    spot: spot === null ? null : `spot ${fmtNum(spot, 2)}`,
    shape: `put BWB${fired ? " + add-on" : ""} · body ${fmtStrike(p.bodyStrike)}`,
    chips: (
      <>
        <span className="chip">{p.arm}</span>
        {p.belowFlipSeen ? <span className="chip chip-warn">below flip</span> : null}
        {fired ? (
          <span className="chip chip-warn" title={`add-on fired ${p.addonFiredAt ?? ""}`}>
            fired
          </span>
        ) : p.armedAt !== null ? (
          <span className="chip" title={`armed ${p.armedAt}`}>
            armed
          </span>
        ) : null}
      </>
    ),
    cash: p.entryCash,
    side: [`${fmtPrice(p.entryCredit)} credit`, `qty ${p.quantity === null ? "—" : String(p.quantity)}`],
    bar: null,
    barTitle: "",
    hideBar: true,
    label: p.unrealisedNet === null ? "no usable mark yet" : `marked net ${fmtMoney(p.unrealisedNet)} · after costs to date`,
    open: { date: p.entrySession === "" ? "—" : p.entrySession.slice(5), price: fmtPrice(p.entryCredit) },
    legs,
    after: [
      `expires ${p.expiration ?? "—"}`,
      p.currentCloseCost === null ? "no close cost yet" : `costs ${fmtMoney(p.currentCloseCost)}/share to close`,
      ...(p.addOnCash === null ? [] : [`add-on ${fmtCash(p.addOnCash)}`]),
      ...(p.peakAbsDelta === null ? [] : [`peak |delta| ${fmtNum(p.peakAbsDelta, 3)}`]),
    ],
    foot: [`fees to date ${p.feesToDate === null ? "—" : fmtMoney(p.feesToDate)}`, p.status],
  };
}

/** Every open BWB, newest session first; symbol and expiry identify the trade, the arm filter narrows it. */
export function OpenTradesCard({ data, updatedAt }: { data: BwbPayload | undefined; updatedAt?: number }) {
  const [arm, setArm] = useState<string | null>(null);
  if (data === undefined) return null;
  const arms = [...new Set(data.openPositions.map((p) => p.arm))].sort();
  const rows = data.openPositions.filter((p) => arm === null || p.arm === arm);
  return (
    <TradeListCard
      card="bwb-open-trades"
      title={arm === null ? "open trades" : `open trades · ${arm} (${String(rows.length)})`}
      help={OPEN_HELP}
      rows={rows}
      model={bwbPositionModel}
      sorts={BWB_OPEN_SORTS}
      fallback="time"
      empty="no open trades -- the daily ladder accumulates one BWB per arm per session"
      updatedAt={updatedAt}
      controls={<ArmSelect module="bwb" value={arm} options={arms} onChange={setArm} />}
    />
  );
}

const firesHelp = (caveat: string): CardHelpContent => ({
  title: "Fire counts",
  body: (
    <>
      <HelpSection title="Why fires, not trades">
        <p>Until an arm&apos;s add-on fires, its positions are the same as control&apos;s, so the effective sample for an arm against control is its fire count.</p>
      </HelpSection>
      <HelpSection title="Correlated rows">
        <p>{caveat}</p>
      </HelpSection>
    </>
  ),
});

/** Per-arm add-on fire counts -- the plan's own honesty rule: the effective sample per arm is the
 * FIRE count, not the trade count. */
export function FireCountsCard({ counts, correlationCaveat, updatedAt }: { counts: BwbFireCount[]; correlationCaveat: string; updatedAt?: number }) {
  const arms = counts.filter((c) => c.arm !== "control");
  return (
    <Card title="add-on fire counts -- the effective sample per arm" collapseKey="bwb-fires" updatedAt={updatedAt} help={firesHelp(correlationCaveat)} flag="rows are not independent">
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
    </Card>
  );
}
