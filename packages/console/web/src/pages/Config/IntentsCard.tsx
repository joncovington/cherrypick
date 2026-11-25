import { useState } from "react";
import type { ConfigModelPayload } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection } from "../../components/CardHelp";
import { pushToast } from "../../lib/toast";
import {
  INTENTS,
  INTENT_WORDS,
  POSITIONS,
  TRADES,
  effectivePositions,
  effectiveTrades,
  intentEdits,
  intentWords,
  pendingIntents,
  pendingTradeIntents,
  savedPositions,
  savedTrades,
  stageIntent,
  stageTradeIntent,
} from "./intents";
import { clearStaged, useStagedVersion } from "./stagedStore";
import { ConfigError, useSaveSection } from "./useConfigModel";

/**
 * Config › Position rules › Intents (2026-11-12): what each trade, and each name, is meant to be, read by
 * the evaluation before it judges (a wheel put may be assigned; a pmcc's diagonal is read as a PMCC). A
 * trade's own intent (`trades.<ID>.intent`, or `trades.<long contract>.intent` for a PMCC) is read over
 * its name's, and a name's counts only for the trades it describes. A choice is staged here or from Live
 * → wheel and saved here, through the orchestrator's config editor with its backup, as the whole
 * `positions` and/or `trades` object.
 */

/** A PMCC's long contract, as `trades` is keyed for the PMCC view (spaces ignored). */
const isContract = (key: string) => /\d{6}[CP]\d{8}$/.test(key.replace(/\s+/g, "").toUpperCase());

/** The intents a select offers, with a saved value outside them (a label such as hedge) kept so a
 *  re-render never silently shows it as none. */
function optionsFor(current: string, base: readonly string[]): string[] {
  return current !== "" && !base.includes(current) ? [...base, current] : [...base];
}

export function IntentsCard({ model, updatedAt }: { model: ConfigModelPayload | undefined; updatedAt?: number }) {
  useStagedVersion();
  const save = useSaveSection();
  const [name, setName] = useState("");
  const [why, setWhy] = useState<string | null>(null);
  const saved = savedPositions(model);
  const eff = effectivePositions(model);
  const savedT = savedTrades(model);
  const effT = effectiveTrades(model);
  if (model?.targets.manage === undefined) return null;

  const pending = pendingIntents(model);
  const pendingFor = new Set(pending.map((p) => p.symbol));
  const pendingT = pendingTradeIntents(model);
  const pendingTFor = new Set(pendingT.map((p) => p.key));
  // Every name and trade with an intent, and any being changed (one set back to none stays until saved).
  const rows = [
    ...new Set([
      ...Object.entries(eff ?? {})
        .filter(([sym, entry]) => !sym.startsWith("_") && typeof entry?.["intent"] === "string")
        .map(([sym]) => sym),
      ...pendingFor,
    ]),
  ].sort();
  const tradeRows = [
    ...new Set([
      ...Object.entries(effT ?? {})
        .filter(([key, entry]) => !key.startsWith("_") && typeof entry?.["intent"] === "string")
        .map(([key]) => key),
      ...pendingTFor,
    ]),
  ].sort();
  const unsaved = pending.length + pendingT.length;
  const err = save.error as ConfigError | Error | null;

  const choose = (sym: string, value: string) => {
    setWhy(stageIntent(model, sym, value === "" ? null : value));
  };
  const chooseTrade = (key: string, value: string) => {
    setWhy(stageTradeIntent(model, key, value === "" ? null : value));
  };
  const discard = () => clearStaged([{ target: "manage", pointer: POSITIONS }, { target: "manage", pointer: TRADES }]);

  return (
    <Card
      title="Intents"
      className="lv-12"
      collapseKey="config-intents"
      updatedAt={updatedAt}
      help={{
        title: "Intents",
        body: (
          <>
            <HelpSection title="What an intent does">
              <p>
                wheel: a tested short put may be assigned, so it is not stopped or closed as breached. not a wheel: the put keeps its stop, and the
                wheel tab stops asking. pmcc and diagonal decide whether a call diagonal is read as a PMCC. Any other word is a label shown on the
                trade.
              </p>
            </HelpSection>
            <HelpSection title="A trade, or a name">
              <p>
                An intent is a fact about one trade, so the wheel tab sets it on the trade itself and it never touches another trade on the same
                name. A name&apos;s intent counts only for the trades it can describe: wheel and not a wheel for a naked short put, pmcc and
                diagonal for a trade with a call in it. A trade&apos;s own intent is read over its name&apos;s.
              </p>
            </HelpSection>
            <HelpSection title="Where it is kept">
              <p>
                <code>trades.&lt;TRADE&gt;.intent</code> and <code>positions.&lt;NAME&gt;.intent</code> in{" "}
                <code>~/.cherrypick/config/manage.json</code>. A file written before the trades block existed gets it on the first save. A choice
                made on Live → wheel waits here until you save.
              </p>
            </HelpSection>
          </>
        ),
      }}
    >
      {saved === null && savedT === null ? (
        <p className="muted">manage.json is missing, so an intent cannot be added from here.</p>
      ) : (
        <>
          <div className="cfg-fields">
            {rows.length === 0 && tradeRows.length === 0 && <p className="muted">No name or trade has an intent yet.</p>}
            {saved === null && <p className="muted">manage.json has no positions block, so a name&apos;s intent cannot be added from here.</p>}
            {rows.map((sym) => (
              <div className="cfg-row" key={sym}>
                <div className="cfg-row-label">
                  <label htmlFor={`intent-${sym}`}>
                    <span className="num">{sym}</span>
                    {pendingFor.has(sym) && <span className="chip chip-warn"> unsaved</span>}
                  </label>
                  <p className="cfg-help">every trade on the name it describes</p>
                </div>
                <div className="cfg-row-control">
                  <select id={`intent-${sym}`} value={String(eff?.[sym]?.["intent"] ?? "")} onChange={(e) => choose(sym, e.target.value)}>
                    <option value="">none</option>
                    {optionsFor(String(eff?.[sym]?.["intent"] ?? ""), INTENTS).map((i) => (
                      <option key={i} value={i}>
                        {(INTENT_WORDS as Record<string, string>)[i] ?? i}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            ))}
            {tradeRows.map((key) => {
              const current = String(effT?.[key]?.["intent"] ?? "");
              const base = isContract(key) ? INTENTS.filter((i) => i === "pmcc" || i === "diagonal") : INTENTS;
              return (
                <div className="cfg-row" key={key}>
                  <div className="cfg-row-label">
                    <label htmlFor={`intent-trade-${key}`}>
                      <span className="num">{key}</span>
                      {pendingTFor.has(key) && <span className="chip chip-warn"> unsaved</span>}
                    </label>
                    <p className="cfg-help">{isContract(key) ? "this PMCC's long only" : "this trade only"}</p>
                  </div>
                  <div className="cfg-row-control">
                    <select id={`intent-trade-${key}`} value={current} onChange={(e) => chooseTrade(key, e.target.value)}>
                      <option value="">none</option>
                      {optionsFor(current, base).map((i) => (
                        <option key={i} value={i}>
                          {(INTENT_WORDS as Record<string, string>)[i] ?? i}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              );
            })}
            {saved !== null && (
              <div className="cfg-row">
                <div className="cfg-row-label">
                  <label htmlFor="intent-add">Add a name</label>
                  <p className="cfg-help">for a name you will wheel before you hold it</p>
                </div>
                <div className="cfg-row-control">
                  <input
                    id="intent-add"
                    value={name}
                    placeholder="ticker"
                    onChange={(e) => setName(e.target.value.toUpperCase().replace(/[^A-Z0-9./]/g, ""))}
                  />
                  <button
                    type="button"
                    className="btn"
                    disabled={name === ""}
                    onClick={() => {
                      choose(name, "wheel");
                      setName("");
                    }}
                  >
                    add as wheel
                  </button>
                </div>
              </div>
            )}
            {why !== null && <p className="cfg-save-error">{why}</p>}
          </div>
          {unsaved > 0 && (
            <div className="cfg-savebar">
              <div className="cfg-savebar-changes">
                <strong>
                  {unsaved} unsaved intent{unsaved === 1 ? "" : "s"}
                </strong>
                <ul>
                  {pendingT.map((p) => (
                    <li key={`t-${p.key}`}>
                      <code>trades.{p.key}.intent</code> {intentWords(p.from)} → <strong>{intentWords(p.to)}</strong>
                    </li>
                  ))}
                  {pending.map((p) => (
                    <li key={`p-${p.symbol}`}>
                      <code>positions.{p.symbol}.intent</code> {intentWords(p.from)} → <strong>{intentWords(p.to)}</strong>
                    </li>
                  ))}
                </ul>
                {err !== null && (
                  <p className="cfg-save-error">
                    {err instanceof ConfigError && err.code === "conflict"
                      ? "manage.json changed on disk since the page loaded — it has been reloaded. Check the intents and save again."
                      : err.message}
                  </p>
                )}
              </div>
              <div className="cfg-savebar-actions">
                <button type="button" className="btn btn-quiet" onClick={discard}>
                  Discard
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={save.isPending}
                  onClick={() =>
                    save.mutate(
                      { target: "manage", expectedMtime: model.targets.manage?.mtime ?? null, edits: intentEdits(model) },
                      {
                        onSuccess: (res) => {
                          discard();
                          const note = res.backup !== null ? `Saved. Previous version backed up to ${res.backup}` : "Saved.";
                          pushToast({ tone: "exit", title: "Intents saved", message: note });
                        },
                      },
                    )
                  }
                >
                  {save.isPending ? "saving…" : "Save intents"}
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
