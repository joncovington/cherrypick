import { Link } from "react-router-dom";
import type { ManageIntentAsk, ManageTrade } from "@console/shared";
import { ACTION_LABEL } from "./manageParts";
import { useIntentAnswer } from "./intentAnswers";
import { QUESTION, answerTrade, intentLabel, restoreTo } from "./stripAnswer";

/**
 * The opened trade's Intent tab: what manage asks about the trade's intent, what it holds now, and each
 * answer beside the verdict the trade would get under it (a put), so a person sees what an answer changes
 * before giving it. It is the card strip's fuller form and shares its saved state (`intentAnswers`): an
 * answer given here is what the card then shows. A tap writes one value through the narrow save; manage
 * reads it at its next evaluation, and a suggestion is never applied. A put answered on its name (its ID
 * is still provisional) shows the same comparison and points to Config for the write.
 */
export function IntentTab({ t }: { t: ManageTrade }) {
  const ask = t.intentAsk;
  const trade = ask ? answerTrade(ask) : null;
  const [state, send] = useIntentAnswer(trade ?? "", ask ?? EMPTY_ASK);
  if (!ask)
    return (
      <p className="muted">
        This trade has no intent to give: only a naked short put (a wheel or
        not) and a call diagonal (a PMCC or not) are asked.
      </p>
    );

  const held = state.saved?.intent ?? ask.current;
  const back = state.saved
    ? restoreTo(state.saved.previous, ask.options)
    : undefined;
  const why = [...ask.evidence, ask.note].filter(Boolean).join(" · ");
  return (
    <div className="int-tab">
      <p className="int-tab-q">{QUESTION[ask.kind]}</p>
      <p className="lk-sub" role="status">
        {held ? (
          <>
            Answered: <strong>{intentLabel(held)}</strong>
            {state.saved ? ". manage reads it at its next evaluation." : "."}
          </>
        ) : (
          "No intent yet."
        )}
        {state.saved && back !== undefined ? (
          <button
            type="button"
            className="btn btn-quiet int-undo"
            disabled={state.busy}
            onClick={() => void send(back)}
          >
            Undo
          </button>
        ) : null}
      </p>
      <div className="int-panel">
        {ask.options.map((opt) => {
          const v = ask.verdicts[opt];
          return (
            <div
              key={opt}
              className={opt === held ? "int-col is-held" : "int-col"}
            >
              <span className="lk-sub">
                {intentLabel(opt)}
                {ask.suggested === opt ? (
                  <span className="chip int-strip-suggested"> suggested</span>
                ) : null}
              </span>
              {v?.action ? (
                <span className="int-verdict">
                  {ACTION_LABEL[v.action] ?? v.action.toLowerCase()}
                  {v.rule ? <span className="muted"> · {v.rule}</span> : null}
                </span>
              ) : null}
              {v?.reason ? <span className="lk-sub">{v.reason}</span> : null}
              {trade !== null ? (
                <button
                  type="button"
                  className="btn"
                  disabled={state.busy || opt === held}
                  aria-pressed={opt === held}
                  onClick={() => void send(opt)}
                >
                  {opt === held ? "Current" : intentLabel(opt)}
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
      {why ? <p className="lk-sub">{why}</p> : null}
      {state.why ? (
        <p className="int-strip-err" role="alert">
          {state.why}
        </p>
      ) : null}
      {trade === null ? (
        ask.scope === "name" ? (
          <p className="lk-sub">
            Its ID changes once its orders are known, so answer for the name on{" "}
            <Link to="/config/rules" className="lk-sub">
              Config
            </Link>
            .
          </p>
        ) : null
      ) : (
        <p className="lk-sub">
          Written to <code>{ask.key}</code> in manage&apos;s config and read at
          its next evaluation.
        </p>
      )}
    </div>
  );
}

const EMPTY_ASK: ManageIntentAsk = {
  kind: "wheel",
  asked: false,
  current: null,
  options: [],
  verdicts: {},
  suggested: null,
  evidence: [],
  note: null,
  key: "",
  scope: "trade",
};
