import { Link } from "react-router-dom";
import type { ManageAccount, ManageAttentionIntent } from "@console/shared";
import { Card } from "../../components/DataTable";
import { Section } from "../../components/Section";
import { HelpSection } from "../../components/CardHelp";
import { ACCOUNT_PARAM } from "./accountParam";
import { intentLabel } from "./stripAnswer";

/**
 * Live → pmcc's needs a look: the call diagonals manage still asks whether they are a PMCC
 * (`attention.intent`, one per open question), one row each with its legs, manage's suggestion and why, and
 * a link to the trade's Intent tab on positions (the account rides along, so the trade is found on a
 * multi-account page). The wheel's puts are surfaced on Live → wheel (the chip and the "no intent yet" card),
 * which carries their cash and days; this is the diagonals' counterpart. The console works out none of it,
 * and nothing here saves or applies an answer.
 */

/** Where an item is answered: its Intent tab, when it has a trade ID to open. */
export function intentLookLink(item: ManageAttentionIntent, account: string | null): string | null {
  if (item.tradeId === null || item.scope === "name") return null;
  const acct = account === null ? "" : `&${ACCOUNT_PARAM}=${encodeURIComponent(account)}`;
  return `/live/positions?trade=${encodeURIComponent(item.tradeId)}&tab=intent${acct}`;
}

/** The diagonals asked for the account; the section is left out when there are none. */
export const diagonalsAsked = (a: ManageAccount): ManageAttentionIntent[] => (a.attention?.intent ?? []).filter((i) => i.kind === "pmcc");

export function DiagonalIntentLook({ a }: { a: ManageAccount }) {
  const items = diagonalsAsked(a);
  if (items.length === 0) return null;
  return (
    <Section concept="risk" tag="needs a look" question="Which diagonals ask if they are a PMCC?">
      <Card
        title="asked if they are a PMCC"
        className="wh-half int-card-waiting"
        help={{
          title: "Asked if it is a PMCC",
          body: (
            <HelpSection title="Why it is asked">
              <p>
                The legs cannot say whether a call diagonal is a PMCC, so each is asked once. Until it is answered a diagonal is read by the delta its
                long was entered at. Each link opens the trade&apos;s intent tab on Live › positions: manage&apos;s suggestion and why, and one tap to
                save the answer for this trade alone. A suggestion is never applied for you.
              </p>
            </HelpSection>
          ),
        }}
      >
        <ul className="lk-list">
          {items.map((i, n) => {
            const to = intentLookLink(i, a.account);
            return (
              <li key={`${i.tradeId ?? i.underlying}-${n}`} className="lk-li">
                <div className="lk-li-top">
                  <span className="lk-leg">
                    {i.underlying} {i.legs.join(", ")}
                  </span>
                  {to !== null ? (
                    <Link to={to} className="int-chip">
                      answer on positions ›
                    </Link>
                  ) : (
                    <span className="lk-sub">no trade ID yet</span>
                  )}
                </div>
                <span className="lk-sub">
                  {i.suggested !== null ? `looks like: ${intentLabel(i.suggested)}` : "no suggestion"}
                  {i.evidence.length > 0 ? ` (${i.evidence.join("; ")})` : ""}
                  {i.note !== null ? ` · ${i.note}` : ""}
                </span>
              </li>
            );
          })}
        </ul>
      </Card>
    </Section>
  );
}
