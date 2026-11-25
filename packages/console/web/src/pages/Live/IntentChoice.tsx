import { Link } from "react-router-dom";
import type { ManageAttentionLeg, ManageIntentCandidate } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection } from "../../components/CardHelp";
import { ACCOUNT_PARAM } from "./accountParam";

/**
 * Live → wheel's "no intent yet": manage lists each short put with no intent of its own
 * (`wheel.intentCandidates`). The question itself is asked on the position (Live › positions: under the
 * trade's card, and on its Intent tab, with the verdict under each answer), so this tab only says which puts
 * wait and where to answer: a needs-a-look row gets a chip, the rest sit in a card of their own. Each is a
 * link to the trade's Intent tab; a put whose ID is still provisional is answered on its name, in Config.
 * Nothing here stages or saves an intent, and nothing judges a trade.
 */

const money = (v: number | null) => (v === null ? "—" : `$${Math.round(v).toLocaleString("en-US")}`);

/** The candidate an attention row belongs to: the same name, expiry and strike, a short put. */
export function candidateFor(cands: ManageIntentCandidate[], leg: ManageAttentionLeg): ManageIntentCandidate | null {
  if (leg.right !== "P" || leg.quantity >= 0 || leg.strike === null) return null;
  return cands.find((c) => c.underlying === leg.underlying && c.expiration === leg.expiration && c.strikes.includes(leg.strike!)) ?? null;
}

/** Where a candidate is answered: its Intent tab once its trade ID is stable (manage keys the answer to the
 *  trade; the account rides along so the trade is found on a multi-account page), else Config, where its
 *  name's intent is set. Null while the name's own intent is for another trade (manage's `note`) and the
 *  trade has no stable ID: an answer on the name would replace that intent, so it waits for the ID. */
function answerAt(c: ManageIntentCandidate, account: string | null): { to: string; where: string } | null {
  if (c.tradeId !== null && c.key === `trades.${c.tradeId}.intent`) {
    const acct = account === null ? "" : `&${ACCOUNT_PARAM}=${encodeURIComponent(account)}`;
    return { to: `/live/positions?trade=${encodeURIComponent(c.tradeId)}&tab=intent${acct}`, where: "answer on positions" };
  }
  return c.note !== null ? null : { to: "/config/rules", where: "answer on Config" };
}

const WAITS = "waits for a stable trade ID: the name's intent is for another trade";

/** The chip on a needs-a-look row: "no intent", linking to where it is answered. */
export function IntentChip({ c, account = null }: { c: ManageIntentCandidate; account?: string | null }) {
  const at = answerAt(c, account);
  if (at === null)
    return (
      <span className="int-chip is-static" title={WAITS}>
        no intent · waits for an ID
      </span>
    );
  return (
    <Link to={at.to} className="int-chip" title={at.where}>
      no intent ›
    </Link>
  );
}

/** The candidates no needs-a-look row shows. Hidden when there are none. */
export function NoIntentCard({
  cands,
  shown,
  account = null,
}: {
  cands: ManageIntentCandidate[];
  shown: ReadonlySet<string>;
  account?: string | null;
}) {
  const rest = cands.filter((c) => !shown.has(candidateKey(c)));
  if (rest.length === 0) return null;
  return (
    <Card
      title="no intent yet"
      className="wh-half int-card-waiting"
      help={{
        title: "No intent yet",
        body: (
          <>
            <HelpSection title="Which trades">
              <p>
                Short puts alone, with no intent of their own, that are not near their strike or expiring. A name&apos;s intent counts only for the
                trades it describes: a name marked pmcc for its diagonal does not answer for a put on the same name. Until you choose, a put keeps its
                stop and its breached-strike close.
              </p>
            </HelpSection>
            <HelpSection title="What a choice does">
              <p>
                Wheel it: a tested put may be assigned instead of stopped. Not a wheel: it keeps its stop and leaves this list. Each link opens the
                trade&apos;s intent tab on Live › positions, which shows the verdict under each answer and saves the one you pick, for this trade
                alone; the evaluation reads it next run. A suggestion is manage&apos;s reading of the name&apos;s wheel history and is never applied
                for you.
              </p>
            </HelpSection>
          </>
        ),
      }}
    >
      <ul className="lk-list">
        {rest.map((c) => {
          const at = answerAt(c, account);
          return (
            <li key={`${c.underlying}-${c.legs.join("|")}`} className="lk-li">
              <div className="lk-li-top">
                <span className="lk-leg">
                  {c.underlying} {c.legs.join(", ")}
                </span>
                {at === null ? (
                  <span className="lk-sub">{WAITS}</span>
                ) : (
                  <Link to={at.to} className="int-chip">
                    {at.where} ›
                  </Link>
                )}
              </div>
              <span className="lk-sub">
                {money(c.cashSecured)} cash secured
                {c.dte !== null ? ` · ${c.dte} DTE` : ""}
                {c.history.cyclesComplete > 0 ? ` · ${c.history.cyclesComplete} cycle${c.history.cyclesComplete === 1 ? "" : "s"} done` : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/** The key NoIntentCard matches a row by. */
export function candidateKey(c: ManageIntentCandidate): string {
  return c.legs.join("|") + c.underlying;
}
