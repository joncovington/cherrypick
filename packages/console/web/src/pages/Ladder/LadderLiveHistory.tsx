import { useState } from "react";
import type { LadderHistoryRow, LadderLivePayload, LadderLiveStatus } from "@console/shared";
import { HelpSection } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { TradeTotalsChip } from "../../components/TradeMoney";
import { HistoryTable } from "../../components/table/HistoryTable";
import { TradeCard } from "../Live/TradeCard";
import { HISTORY_DEFS } from "./LadderPages";
import { LiveLedgerNotes, ledgerLine } from "./LadderLive";
import { lotModel } from "./ladderModels";

/**
 * The ladder's history tab under the frame's Live mode: the sold live lots in the paper history's own
 * columns and the same expandable trade card, over the live ledger (`live_trades.db`). Read only. The one
 * handler is the row's own expand toggle, which holds a page-local state and sends nothing.
 */

function note(title: string, heading: string, text: string) {
  return {
    title,
    body: (
      <HelpSection title={heading}>
        <p>{text}</p>
      </HelpSection>
    ),
  };
}

export function LadderLiveHistory({ live, status }: { live: LadderLivePayload | undefined; status: LadderLiveStatus | undefined }) {
  const [open, setOpen] = useState<string | null>(null);
  const banner = <LiveLedgerNotes live={live} status={status} />;
  const empty = ledgerLine(live, "No live lot has sold yet.");
  if (live !== undefined && live.closedLots.length === 0) {
    return (
      <div className="pf-layout">
        {banner}
        <p className="muted" role="note">
          {empty}
        </p>
      </div>
    );
  }
  const rows = live?.closedLots ?? [];
  const src = live?.feesBySource;
  return (
    <div className="pf-layout">
      {banner}
      <Section concept="hist" tag="the record" question="How did each sold live lot end?">
        <HistoryTable<LadderHistoryRow>
          table="ladder-live-history"
          title="sold live lots"
          className="view-fade lv-12"
          defs={HISTORY_DEFS}
          clientSort
          dateRange={false}
          rows={rows}
          rowKey={(r) => r.positionId}
          loading={live === undefined}
          empty={empty}
          footer={
            live !== undefined && (
              <>
                <TradeTotalsChip totals={live.closedTotals} noun="lots" />
                {src !== undefined && src.modeled + src.broker + src.unrecorded > 0 && (
                  <span className="muted">
                    {" "}
                    · fees: {String(src.modeled)} modeled, {String(src.broker)} from the broker
                    {src.unrecorded > 0 ? `, ${String(src.unrecorded)} not recorded` : ""}
                  </span>
                )}
                {live.closedLots.length < live.closedTotals.positions && (
                  <span className="muted"> · the newest {String(live.closedLots.length)} rows; the totals cover every sold lot</span>
                )}
              </>
            )
          }
          help={note(
            "Sold live lots",
            "Reading a row",
            "Entry is the purchase (negative) and exit the sale (positive), at the broker's fills; they add up to gross. Gross less fees, settlement and slippage is net. Fees are the buy's and the sale's together: modeled is the tastytrade schedule, broker is the broker's own transaction rows, counted in the footer. Settlement and slippage are zero for shares filled at a resting limit. Open a row to see the lot as the buy and the sale that closed it.",
          )}
          rowProps={(r) => ({
            className: "cal-row-click",
            onClick: () => setOpen(open === r.positionId ? null : r.positionId),
          })}
          expanded={(r) =>
            open === r.positionId ? (
              <div className="tc-grid">
                <TradeCard m={lotModel(r)} />
              </div>
            ) : null
          }
          detailClassName="cal-detail-row"
        />
      </Section>
    </div>
  );
}

