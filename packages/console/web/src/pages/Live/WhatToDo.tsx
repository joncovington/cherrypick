import { Link } from "react-router-dom";
import type { ManagePayload } from "@console/shared";
import { Card, PnlCell } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { ACTION_PRIORITY, earningsInLife } from "./ManagePage";
import { GuideLink } from "../../components/GuideLink";

/**
 * The broker positions card on Live > today: a summary of the position evaluation (packages/manage) with
 * a way into the Positions tab, where every trade, rule and mark is. The groups are the evaluation's:
 * the trades it says to act on (its own action and reason, in its own urgency order -- nothing is
 * re-judged here, so the first three are manage's first three), the held positions whose short expires
 * within a week or whose underlying reports before it does, and the income trades that fit the
 * account's room. A live loop that needs attention is the loops card's chip, not a line here. Advice
 * only: nothing here places or changes an order.
 */

/** A held position whose nearest short is this close is listed as expiring. */
export const EXPIRING_DAYS = 7;

/** How many of the trades to act on the card names; the rest are a click away on Positions. */
export const URGENT_SHOWN = 3;

const QUIET = new Set(["HOLD", "SETTLES"]);

const words = (s: string) => s.replace(/_/g, " ").toLowerCase();

/** One trade the evaluation says to act on, as the card names it. */
export interface UrgentRow {
  tradeId: string;
  /** The account, only when the evaluation covers several. */
  account: string | null;
  action: string;
  reason: string;
  /** The evaluation's own net P&L on the trade (`pnlNet`); null when it has none. */
  net: number | null;
}

export interface BrokerSummary {
  /** Trades the evaluation says to act on, across the accounts. */
  act: number;
  /** Held trades whose short expires within `EXPIRING_DAYS`. */
  expiring: number;
  /** Held trades whose underlying reports before the nearest short expires. */
  earnings: number;
  /** Income trades that fit the accounts' room. */
  income: number;
  /** The first `URGENT_SHOWN` trades to act on, in manage's urgency order (`ACTION_PRIORITY`). */
  urgent: UrgentRow[];
}

export function brokerSummary(manage: ManagePayload | undefined): BrokerSummary {
  const accounts = manage?.accounts ?? [];
  const several = accounts.length > 1;
  const acting: UrgentRow[] = [];
  let expiring = 0;
  let earnings = 0;
  let income = 0;
  for (const acct of accounts) {
    for (const t of acct.trades) {
      if (!QUIET.has(t.action)) {
        acting.push({ tradeId: t.tradeId, account: several ? acct.account : null, action: t.action, reason: t.reason, net: t.pnlNet });
      } else {
        if (t.nearestShortDte !== null && t.nearestShortDte <= EXPIRING_DAYS) expiring += 1;
        if (earningsInLife(t)) earnings += 1;
      }
    }
    income += acct.portfolio?.candidates?.fitsTotal ?? 0;
  }
  // Array.sort is stable: trades of equal urgency stay in the evaluation's own order.
  acting.sort((x, y) => (ACTION_PRIORITY[x.action] ?? 9) - (ACTION_PRIORITY[y.action] ?? 9));
  return { act: acting.length, expiring, earnings, income, urgent: acting.slice(0, URGENT_SHOWN) };
}

const ACTION_LABEL: Record<string, string> = {
  ROLL_FOR_CREDIT: "roll",
  ROLL_OR_CLOSE: "roll or close",
  CLOSE: "close",
  CLOSE_LEGS: "close legs",
  TAKE_PROFIT: "take profit",
  REVIEW: "review",
};

/** The chip's words and tone for manage's action: an exit is red, a profit green, a review blue, the rest amber. */
export function actionChip(action: string): { text: string; cls: string } {
  const text = ACTION_LABEL[action] ?? words(action);
  if (action === "CLOSE" || action === "CLOSE_LEGS") return { text, cls: "chip-bad" };
  if (action === "TAKE_PROFIT") return { text, cls: "chip-ok" };
  if (action === "REVIEW") return { text, cls: "chip-hold" };
  return { text, cls: "chip-warn" };
}

export const WHAT_TO_DO_HELP: CardHelpContent = {
  title: "Broker positions",
  body: (
    <>
      <HelpSection title="The counts">
        <p>
          Act on is every trade the position evaluation says to do something about, with its own action and reason. Expiring is a held
          position whose nearest short expires within a week; earnings is one whose underlying reports before that short expires. Income
          counts the trades that fit the account&apos;s room. Each count opens the Positions tab, where every trade, rule and mark is.
        </p>
      </HelpSection>
      <HelpSection title="The first three">
        <p>
          The trades named are the first three in the evaluation&apos;s own urgency order, with its net P&amp;L on each. Nothing is
          re-ranked here.
        </p>
      </HelpSection>
      <HelpSection title="Advice only">
        <p>Advice only: nothing on this card places or changes an order.</p>
      </HelpSection>
      <GuideLink to="/help/live#today" />
    </>
  ),
};

/**
 * `manage` is the position evaluation, or undefined when it has not been read: the card then says so,
 * so an empty summary never reads as "nothing on the account".
 */
export function BrokerSummaryCard({ manage, className }: { manage: ManagePayload | undefined; className?: string }) {
  const evaluated = manage?.session ?? null;
  const s = brokerSummary(manage);
  const tiles: Array<{ key: string; n: number; label: string; to: string }> = [
    { key: "act", n: s.act, label: "act on", to: "/live/positions" },
    { key: "expiring", n: s.expiring, label: "expiring this week", to: "/live/positions" },
    { key: "earnings", n: s.earnings, label: "earnings before expiry", to: "/live/positions" },
    ...(s.income > 0 ? [{ key: "income", n: s.income, label: "income trades fit", to: "/live/portfolio" }] : []),
  ];
  const quiet = tiles.every((t) => t.n === 0);
  return (
    <Card
      title="broker positions"
      className={className}
      help={WHAT_TO_DO_HELP}
      controls={<span className="card-asof">{evaluated !== null ? `as evaluated ${evaluated}` : "positions not read"}</span>}
    >
      {evaluated === null ? (
        <p className="muted">
          The position evaluation has not been read, so no position is summarized here. <Link to="/live/positions">Positions →</Link>
        </p>
      ) : quiet ? (
        <p className="muted">
          No position has an action, an expiry this week or a report before it. <Link to="/live/positions">Positions →</Link>
        </p>
      ) : (
        <>
          <div className="bp-tiles">
            {tiles.map((t) => (
              <Link key={t.key} to={t.to} className={`bp-tile ${t.key === "act" && t.n > 0 ? "bp-tile-act" : ""}`}>
                <span className="bp-n">{t.n}</span>
                <span className="bp-lab">{t.label}</span>
              </Link>
            ))}
          </div>
          {s.urgent.length > 0 && (
            <div className="bp-urgent">
              <div className="bp-head">
                <span>first to act on · manage&apos;s order</span>
                <span>net P&amp;L</span>
              </div>
              <ul>
                {s.urgent.map((u) => {
                  const chip = actionChip(u.action);
                  return (
                    <li key={`${u.account ?? ""}-${u.tradeId}`} title={u.reason}>
                      <Link to="/live/positions" className="bp-id">
                        {u.tradeId}
                      </Link>
                      <span className={`chip ${chip.cls}`}>{chip.text}</span>
                      <span className="bp-reason">
                        {u.reason}
                        {u.account !== null && <span className="muted"> · {u.account}</span>}
                      </span>
                      <span className="bp-net">
                        <PnlCell v={u.net} />
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          <p className="bp-foot">
            <span className="muted">{s.act > URGENT_SHOWN ? `${s.act - URGENT_SHOWN} more to act on, in the same order` : ""}</span>
            <Link to="/live/positions">{s.act > 0 ? `All ${s.act} on Positions →` : "Positions →"}</Link>
          </p>
        </>
      )}
    </Card>
  );
}
