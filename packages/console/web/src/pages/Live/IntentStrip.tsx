import { Link } from "react-router-dom";
import type { ManageIntentAsk, ManageTrade } from "@console/shared";
import { ACTION_LABEL } from "./manageParts";
import { useIntentAnswer } from "./intentAnswers";
import { HELD_FOR_ID, QUESTION, answerTrade, intentLabel, restoreTo } from "./stripAnswer";

/**
 * The question a position asks about its intent: shown only while manage asks it (`intentAsk.asked`), one
 * tap per answer. A tap writes one value through the console's narrow save (`POST /api/config/intent`,
 * `trades.<key>.intent` and nothing else), and the strip then says so, with Undo: manage reads the answer
 * on its next evaluation, so the card does not change until then. A put whose ID is still provisional
 * cannot be answered here (its ID changes once its orders are known); it says so and points to Config.
 * The verdict under each answer, the suggestion and why are manage's. Nothing here judges a trade, and a
 * suggestion is never applied.
 */

export function IntentStrip({ t }: { t: ManageTrade }) {
  const ask = t.intentAsk;
  // No open question, or nothing to answer it with: nothing to show.
  if (!ask || !ask.asked || ask.options.length === 0) return null;
  if (ask.scope === "name") return <WaitingStrip ask={ask} />;
  const trade = answerTrade(ask);
  // A key the save route could not take is a fault in the evaluation, not a question to put.
  return trade === null ? null : <AnswerStrip t={t} ask={ask} trade={trade} />;
}

/** The question with its answers. Its state (saved, with Undo) lasts until manage re-evaluates: the ask
 * still reads open on the next poll, and once the answer is read the question is gone. Nothing refetches
 * on a save, as the evaluation cannot change before manage runs again. */
function AnswerStrip({
  t,
  ask,
  trade,
}: {
  t: ManageTrade;
  ask: ManageIntentAsk;
  trade: string;
}) {
  const [state, send] = useIntentAnswer(trade, ask);

  const failed = state.why ? (
    <span className="int-strip-err" role="alert">
      {state.why}
    </span>
  ) : null;

  if (state.saved) {
    const back = restoreTo(state.saved.previous, ask.options);
    return (
      <div
        className="int-strip is-saved"
        role="status"
        aria-label={`Intent for ${t.tradeId}`}
      >
        <span>
          Answered: <strong>{intentLabel(state.saved.intent)}</strong>. manage
          reads it at its next evaluation.
        </span>
        {back !== undefined ? (
          <button
            type="button"
            className="btn btn-quiet"
            disabled={state.busy}
            onClick={() => void send(back)}
          >
            Undo
          </button>
        ) : null}
        {failed}
      </div>
    );
  }

  return (
    <div
      className="int-strip"
      role="group"
      aria-label={`Intent for ${t.tradeId}`}
    >
      <span className="int-strip-q">{QUESTION[ask.kind]}</span>
      <span className="int-strip-opts">
        {ask.options.map((opt) => {
          const v = ask.verdicts[opt];
          return (
            <button
              key={opt}
              type="button"
              className="btn int-strip-opt"
              disabled={state.busy}
              onClick={() => void send(opt)}
            >
              <span>{intentLabel(opt)}</span>
              {v?.action ? (
                <span className="int-strip-verdict">
                  {ACTION_LABEL[v.action] ?? v.action.toLowerCase()}
                </span>
              ) : null}
              {ask.suggested === opt ? (
                <span className="chip int-strip-suggested">suggested</span>
              ) : null}
            </button>
          );
        })}
      </span>
      <Why ask={ask} />
      {failed}
    </div>
  );
}

function Why({ ask }: { ask: ManageIntentAsk }) {
  const why = [...ask.evidence, ask.note].filter(Boolean).join(" · ");
  return why ? <span className="int-strip-why lk-sub">{why}</span> : null;
}

/** A put with no stable ID yet: asked, but its answer cannot be written under an ID that will change. */
function WaitingStrip({ ask }: { ask: ManageIntentAsk }) {
  return (
    <div className="int-strip is-waiting">
      <span className="int-strip-q">{QUESTION[ask.kind]}</span>
      <span className="lk-sub">
        {ask.note !== null ? (
          HELD_FOR_ID
        ) : (
          <>
            Its ID changes once its orders are known, so answer for the name on{" "}
            <Link to="/config/rules" className="lk-sub">
              Config
            </Link>
            .
          </>
        )}
      </span>
    </div>
  );
}
