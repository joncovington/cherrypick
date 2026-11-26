import { createContext, useContext } from "react";
import { Link } from "react-router-dom";
import type { ManageIntentAsk, ManagePmccPosition, ManageTrade } from "@console/shared";
import { ACCOUNT_PARAM } from "./accountParam";
import { useIntentAnswer } from "./intentAnswers";
import { answerTrade, intentLabel, restoreTo } from "./stripAnswer";

/**
 * The PMCC card's intent row: for a call diagonal manage still asks whether it is a PMCC, the choice itself
 * (one tap, the same save and Undo as the position's strip and Intent tab, sharing their saved state); for one
 * already answered, what is set and a link to change it on positions. The question and its suggestion are
 * manage's (`trade.intentAsk`); nothing here works out whether a diagonal is a PMCC or applies a suggestion.
 * Where the choice cannot be offered the row keeps what it had: the words and the key to set by hand.
 */

/** The account the PMCC tab is drawing, so a link to a trade opens it on the account that holds it. */
export const PmccAccount = createContext<string | null>(null);

/** A contract key as manage compares it: spaces and case ignored. */
const norm = (s: string) => s.replace(/\s+/g, "").toUpperCase();

export interface PmccAsk {
  ask: ManageIntentAsk;
  tradeId: string;
  provisional: boolean;
}

/** The question manage asks about this PMCC's long, with the trade that carries it: the ask whose key is
 *  `trades.<the long's contract>.intent`. Null when there is none (an evaluation written before it). */
export function askFor(p: ManagePmccPosition, trades: ManageTrade[]): PmccAsk | null {
  if (!p.intentKey) return null;
  const key = norm(`trades.${p.intentKey}.intent`);
  for (const t of trades) {
    if (p.tradeIds.includes(t.tradeId) && t.intentAsk && norm(t.intentAsk.key) === key) {
      return { ask: t.intentAsk, tradeId: t.tradeId, provisional: t.tradeIdProvisional };
    }
  }
  return null;
}

/** `words` is what is set; `fallback` is the row as it was (the words and the key), for where the choice
 *  cannot be offered. */
export function PmccIntentChoice({ found, words, fallback }: { found: PmccAsk; words: string; fallback: string }) {
  const { ask, tradeId, provisional } = found;
  const trade = answerTrade(ask);
  const account = useContext(PmccAccount);
  const [state, send] = useIntentAnswer(trade ?? "", ask);
  if (trade === null || ask.options.length === 0) return <span>{fallback}</span>;

  if (state.saved) {
    const back = restoreTo(state.saved.previous, ask.options);
    return (
      <span className="pmcc-intent" role="status">
        Answered: <strong>{intentLabel(state.saved.intent)}</strong>. manage reads it at its next evaluation.
        {back !== undefined ? (
          <button type="button" className="btn btn-quiet int-undo" disabled={state.busy} onClick={() => void send(back)}>
            Undo
          </button>
        ) : null}
        {state.why ? <span className="int-strip-err"> {state.why}</span> : null}
      </span>
    );
  }
  if (!ask.asked) {
    // Already answered: what is set, and where to change it (a trade with no stable ID has nothing to open).
    const acct = account === null ? "" : `&${ACCOUNT_PARAM}=${encodeURIComponent(account)}`;
    return (
      <span className="pmcc-intent">
        {words}
        {" · "}
        {provisional ? (
          <span className="lk-sub">no trade ID yet</span>
        ) : (
          <Link to={`/live/positions?trade=${encodeURIComponent(tradeId)}&tab=intent${acct}`} className="lk-sub">
            change on positions
          </Link>
        )}
      </span>
    );
  }
  return (
    <span className="pmcc-intent">
      <span>{words}</span>
      <span className="int-strip-opts">
        {ask.options.map((opt) => (
          <button key={opt} type="button" className="btn int-strip-opt" disabled={state.busy} onClick={() => void send(opt)}>
            <span>{intentLabel(opt)}</span>
            {ask.suggested === opt ? <span className="chip int-strip-suggested">suggested</span> : null}
          </button>
        ))}
      </span>
      {ask.evidence.length > 0 ? <span className="lk-sub"> {ask.evidence.join("; ")}</span> : null}
      {state.why ? <span className="int-strip-err"> {state.why}</span> : null}
    </span>
  );
}
