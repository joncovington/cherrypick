import type { ReactNode } from "react";
import { feeDragPct, type ArmMoneyRow, type EntryOutcomes } from "@console/shared";
import { Card, DataCard, PnlCell, SkeletonRows, fmtPct } from "../DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../CardHelp";
import { Section } from "../Section";
import { Tile } from "../performance/Tile";
import { TileGrid } from "../performance/TileGrid";
import { useOpenParam } from "../../pages/Backtest/CardLightbox";
import { BoxOpen, FliesBoxHost, type FliesBox } from "../../pages/Flies/FliesBoxes";
import { fmtMoney } from "../../lib/format";

/**
 * A module's costs page body (curve 2026-10-05, pmcc 2026-10-06): where the premium went, and how
 * often the gates let an entry through.
 *
 * Fee drag is trading fees ÷ premium collected (docs/conventions/money-layout.md), named that way; "all costs ÷
 * premium" adds settlement and slippage beside it, never inside it. Entry outcomes are counted per
 * SESSION (`readers/armCosts.ts`), so a gate that refused every tick is one session.
 *
 * Laid out under the two questions (2026-11-07). The same table over every session on file is a
 * lightbox from the gates card (`?cost=`).
 */

const share = (part: number, whole: number): number | null => (whole > 0 ? (part / whole) * 100 : null);

function OutcomeRows({ rows }: { rows: EntryOutcomes[] }) {
  return (
    <>
      {rows.map((o) => {
        const top = Object.entries(o.refusals).sort((a, b) => b[1] - a[1]);
        return (
          <tr key={o.arm}>
            <td>{o.arm}</td>
            <td>{o.sessions}</td>
            <td>{o.entered}</td>
            <td>{o.fills}</td>
            <td>{fmtPct(share(o.entered, o.sessions), 0)}</td>
            <td className="muted">{top.length === 0 ? "—" : top.map(([reason, n]) => `${reason} ${String(n)}`).join(" · ")}</td>
          </tr>
        );
      })}
    </>
  );
}

const OUTCOME_HEADERS = ["arm", "sessions", "entered", "fills", "entry rate", "the refusals that ended the other sessions"];

/** The entry-outcome table, for the gates card and for the lightbox over every session. */
function OutcomeTable({ rows, loading, empty }: { rows: EntryOutcomes[]; loading: boolean; empty: string }) {
  return (
    <div className="table-scroll">
      <table className="data-table num-from-1">
        <thead>
          <tr>
            {OUTCOME_HEADERS.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <SkeletonRows n={6} cols={OUTCOME_HEADERS.length} />
          ) : rows.length === 0 ? (
            <tr>
              <td colSpan={OUTCOME_HEADERS.length} className="muted">
                {empty}
              </td>
            </tr>
          ) : (
            <OutcomeRows rows={rows} />
          )}
        </tbody>
      </table>
    </div>
  );
}

function premiumHelp(premiumNote: string, slippageInGross: boolean): CardHelpContent {
  return {
    title: "Where the premium went",
    body: (
      <>
        <HelpSection title="Fee drag">
          <p>Trading fees ÷ premium collected, over the closed positions of every arm. Settlement and slippage sit beside it, never inside it.</p>
        </HelpSection>
        <HelpSection title={slippageInGross ? "Slippage ÷ premium" : "All costs ÷ premium"}>
          <p>
            {slippageInGross
              ? "Slippage is conceded inside gross by the modelled fill, so it is a measure and is never subtracted again."
              : "Fees + settlement + slippage, each its own column in the table."}
          </p>
        </HelpSection>
        <HelpSection title="Premium collected">
          <p>{premiumNote}.</p>
        </HelpSection>
      </>
    ),
  };
}

function gatesHelp(sessionsSince: number, since: string | null, gateNote: ReactNode): CardHelpContent {
  const few = sessionsSince > 0 && sessionsSince < 20;
  return {
    title: "Reading the gates",
    body: (
      <>
        {few && (
          <HelpWarn title="Too few sessions">
            <p>
              Only {String(sessionsSince)} sessions since {since ?? "then"}: too few to judge a gate on.
            </p>
          </HelpWarn>
        )}
        <HelpSection title="Counted per session">
          <p>
            Not per tick: a gate that refused all morning is one session here. A session is entered if any tick filled; otherwise it counts
            under its last refusal.
          </p>
        </HelpSection>
        <HelpSection title="This module">
          <p>{gateNote}</p>
        </HelpSection>
      </>
    ),
  };
}

export function CostsView({
  arms,
  slippageInGross = false,
  since,
  sinceLabel,
  sinceRows,
  allRows,
  loading,
  premiumNote,
  gateNote,
  updatedAt,
}: {
  arms: ArmMoneyRow[];
  /** The modelled fill already concedes slippage (flies, meic): it is inside gross, shown as a
   *  measure and never subtracted again. Otherwise it is a charged cost column. */
  slippageInGross?: boolean;
  since: string | null;
  /** What `since` is: "the boundary", "the era's first entry". */
  sinceLabel: string;
  sinceRows: EntryOutcomes[];
  allRows: EntryOutcomes[];
  loading: boolean;
  /** What "premium" is for this module. */
  premiumNote: string;
  /** What a reader should know about this module's gates. */
  gateNote: ReactNode;
  updatedAt?: number;
}) {
  const lb = useOpenParam("cost");
  const premium = arms.reduce((t, a) => t + a.premium, 0);
  const fees = arms.reduce((t, a) => t + a.fees, 0);
  const charged = (a: ArmMoneyRow) => a.fees + a.settlementFees + (slippageInGross ? 0 : a.slippage);
  const allCosts = arms.reduce((t, a) => t + charged(a), 0);
  const slipTotal = arms.reduce((t, a) => t + a.slippage, 0);
  const cycles = arms.reduce((t, a) => t + a.positions, 0);
  const sessionsSince = sinceRows.reduce((t, o) => Math.max(t, o.sessions), 0);
  const few = sessionsSince > 0 && sessionsSince < 20;
  const box: FliesBox = {
    id: "every",
    kicker: "entries ›",
    title: "Every session on file",
    tabs: [{ id: "all", label: "every session" }],
    body: () => <OutcomeTable rows={allRows} loading={loading} empty="no entry attempt recorded" />,
  };
  return (
    <div className="pf-layout">
      <Section concept="income" tag="costs" question="Where did the premium go?">
        <Card title="premium and costs" className="lv-12" collapseKey="costs-premium" help={premiumHelp(premiumNote, slippageInGross)}>
          <TileGrid count={4}>
            <Tile label="fee drag" value={cycles === 0 ? "—" : fmtPct(feeDragPct(fees, premium), 1)} tone="dim" title="trading fees ÷ premium collected, closed positions" />
            {slippageInGross ? (
              <Tile
                label="slippage ÷ premium"
                value={cycles === 0 ? "—" : fmtPct(share(slipTotal, premium), 1)}
                tone="dim"
                title="conceded inside gross by the modelled fill -- a measure, not subtracted again"
              />
            ) : (
              <Tile label="all costs ÷ premium" value={cycles === 0 ? "—" : fmtPct(share(allCosts, premium), 1)} tone="dim" title="fees + settlement + slippage, each its own column below" />
            )}
            <Tile label="cost per position" value={cycles === 0 ? "—" : fmtMoney(allCosts / cycles)} tone="dim" n={cycles} nUnit="closed positions" />
            <Tile label="premium collected" value={cycles === 0 ? "—" : fmtMoney(premium)} tone="dim" title={premiumNote} />
          </TileGrid>
        </Card>

        <DataCard
          title="where the premium went, by arm"
          className="lv-12"
          headers={
            slippageInGross
              ? ["arm", "closed", "premium", "gross", "fees", "settle", "net", "fee drag", "slip (in gross)", "slip ÷ premium"]
              : ["arm", "closed", "premium", "gross", "fees", "settle", "slip", "net", "fee drag", "all costs ÷ premium"]
          }
          loading={loading}
          rowCount={arms.length}
          numFrom={1}
          empty="no position has closed yet"
          updatedAt={updatedAt}
        >
          {arms.map((a) => (
            <tr key={a.arm}>
              <td>{a.arm}</td>
              <td>{a.positions}</td>
              <td>{fmtMoney(a.premium)}</td>
              <td>
                <PnlCell v={a.grossPnl} />
              </td>
              <td>{fmtMoney(-a.fees)}</td>
              <td>{fmtMoney(-a.settlementFees)}</td>
              {!slippageInGross && <td>{fmtMoney(-a.slippage)}</td>}
              <td>
                <PnlCell v={a.netPnl} />
              </td>
              <td>{fmtPct(feeDragPct(a.fees, a.premium), 1)}</td>
              {slippageInGross ? (
                <>
                  <td className="muted">{fmtMoney(a.slippage)}</td>
                  <td>{fmtPct(share(a.slippage, a.premium), 1)}</td>
                </>
              ) : (
                <td>{fmtPct(share(charged(a), a.premium), 1)}</td>
              )}
            </tr>
          ))}
        </DataCard>
      </Section>

      <Section concept="add" tag="entries" question="How often did the gates let an entry through?">
        <Card
          title={since === null ? "entry outcomes" : `entry outcomes, since ${since} (${sinceLabel})`}
          className="lv-12"
          collapseKey="costs-gates"
          help={gatesHelp(sessionsSince, since, gateNote)}
          flag={few ? "few sessions" : undefined}
        >
          <OutcomeTable
            rows={sinceRows}
            loading={loading}
            empty={since === null ? "no entry attempt recorded" : `no session evaluated since ${since} yet`}
          />
          {since !== null && (
            <div className="pf-mores">
              <BoxOpen lb={lb} id="every" tab="all">
                the same, over every session on file
              </BoxOpen>
            </div>
          )}
        </Card>
      </Section>
      {since !== null && <FliesBoxHost lb={lb} boxes={[box]} />}
    </div>
  );
}
