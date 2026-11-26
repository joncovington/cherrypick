import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import type { ConfigModelPayload } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection } from "../../components/CardHelp";
import { mutateJson } from "../../lib/api";
import { pushToast } from "../../lib/toast";
import { saveIntent } from "../Live/stripAnswer";
import {
  INTENTS,
  discardIntents,
  effectivePositions,
  intentEdits,
  intentWords,
  pendingIntents,
  savedPositions,
  stageIntent,
  tradeIntents,
} from "./intents";
import { useStagedVersion } from "./stagedStore";
import { ConfigError, useSaveSection } from "./useConfigModel";

/**
 * Config › Position rules › Intents: what each name and each trade is meant to be, read by the evaluation
 * before it judges (a wheel put may be assigned; a pmcc's diagonal is read as a PMCC). A trade's own intent
 * (`trades.<ID>.intent`, or `trades.<long contract>.intent` for a PMCC) is read over its name's, and a name's
 * counts only for the trades it describes. The names are edited here, staged and saved through the
 * orchestrator's config editor with its backup, as the whole `positions` object. The trades are listed, to
 * see them all: a trade's intent is answered on the position (Live › positions), and the one thing done here
 * is clearing an entry.
 */

/** A PMCC's long call contract, as `trades` is keyed for the PMCC view (spaces ignored): the same test as
 *  manage's `_is_call_contract`, which the console cannot call. */
const isContract = (key: string) => /\d{6}C\d{8}$/.test(key.replace(/\s+/g, "").toUpperCase());

/** The intents a select offers, with a saved value outside them (a label such as hedge) kept so a
 *  re-render never silently shows it as none. */
function optionsFor(current: string, base: readonly string[]): string[] {
  return current !== "" && !base.includes(current) ? [...base, current] : [...base];
}

/** One name's row: what the intent is for, and the select that sets it. */
function IntentRow({
  id,
  label,
  help,
  current,
  unsaved,
  onChange,
}: {
  id: string;
  label: string;
  help: string;
  current: string;
  unsaved: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <div className="cfg-row">
      <div className="cfg-row-label">
        <label htmlFor={id}>
          <span className="num">{label}</span>
          {unsaved && <span className="chip chip-warn"> unsaved</span>}
        </label>
        <p className="cfg-help">{help}</p>
      </div>
      <div className="cfg-row-control">
        <select id={id} value={current} onChange={(e) => onChange(e.target.value)}>
          <option value="">none</option>
          {optionsFor(current, INTENTS).map((i) => (
            <option key={i} value={i}>
              {intentWords(i)}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

/** One trade's row: the key it is held under and what it is, with where to change it. A trade ID opens that
 *  trade's Intent tab on Live › positions; a PMCC's long contract is changed from its card. The one thing
 *  done here is clearing an entry (through the same narrow save as an answer on the position), which is how
 *  one left behind by a closed trade is removed. */
function TradeRow({ tradeKey, intent }: { tradeKey: string; intent: string }) {
  const contract = isContract(tradeKey);
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [why, setWhy] = useState<string | null>(null);
  const clear = async () => {
    setBusy(true);
    setWhy(null);
    const r = await saveIntent(tradeKey, null, mutateJson);
    setBusy(false);
    if (!r.ok) return setWhy(r.why);
    pushToast({ tone: "exit", title: "Intent cleared", message: tradeKey });
    void qc.invalidateQueries({ queryKey: ["config-model"] });
  };
  return (
    <div className="cfg-row">
      <div className="cfg-row-label">
        <span className="num">{tradeKey}</span>
        <p className="cfg-help">{contract ? "this PMCC's long only" : "this trade only"}</p>
      </div>
      <div className="cfg-row-control">
        <span className="chip">{intentWords(intent)}</span>
        <Link to={contract ? "/live/pmcc" : `/live/positions?trade=${encodeURIComponent(tradeKey)}&tab=intent`} className="lk-sub">
          {contract ? "change on pmcc" : "change on positions"}
        </Link>
        <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => void clear()}>
          clear
        </button>
        {why !== null && <span className="cfg-save-error">{why}</span>}
      </div>
    </div>
  );
}

export function IntentsCard({ model, updatedAt }: { model: ConfigModelPayload | undefined; updatedAt?: number }) {
  useStagedVersion();
  const save = useSaveSection();
  const [name, setName] = useState("");
  const [why, setWhy] = useState<string | null>(null);
  const saved = savedPositions(model);
  const eff = effectivePositions(model);
  if (model?.targets.manage === undefined) return null;

  const pending = pendingIntents(model);
  const pendingFor = new Set(pending.map((p) => p.symbol));
  const trades = tradeIntents(model);
  // Every name with an intent, and any being changed (one set back to none stays until saved).
  const rows = [
    ...new Set([
      ...Object.entries(eff ?? {})
        .filter(([sym, entry]) => !sym.startsWith("_") && typeof entry?.["intent"] === "string")
        .map(([sym]) => sym),
      ...pendingFor,
    ]),
  ].sort();
  const unsaved = pending.length;
  const err = save.error as ConfigError | Error | null;

  const choose = (sym: string, value: string) => {
    setWhy(stageIntent(model, sym, value === "" ? null : value));
  };

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
                An intent is a fact about one trade, so it is answered on the trade itself (Live › positions: the question under its card, or its
                intent tab) and never touches another trade on the same name. The trades with an intent are listed here, and an entry can be cleared (one left by a closed trade, say). A name&apos;s
                intent counts only for the trades it can describe: wheel and not a wheel for a naked short put, pmcc and diagonal for a trade with a
                call in it. A trade&apos;s own intent is read over its name&apos;s. A name&apos;s intent is edited here.
              </p>
            </HelpSection>
            <HelpSection title="Where it is kept">
              <p>
                <code>trades.&lt;TRADE&gt;.intent</code> and <code>positions.&lt;NAME&gt;.intent</code> in{" "}
                <code>~/.cherrypick/config/manage.json</code>. A trade&apos;s is written the moment it is answered on the position, with a backup of
                the file; a name&apos;s waits here until you save.
              </p>
            </HelpSection>
          </>
        ),
      }}
    >
      {model.targets.manage.exists !== true ? (
        <p className="muted">manage.json is missing, so an intent cannot be added from here.</p>
      ) : (
        <>
          <div className="cfg-fields">
            {rows.length === 0 && trades.length === 0 && <p className="muted">No name or trade has an intent yet.</p>}
            {saved === null && <p className="muted">manage.json has no positions block, so a name&apos;s intent cannot be added from here.</p>}
            {rows.map((sym) => (
              <IntentRow
                key={sym}
                id={`intent-${sym}`}
                label={sym}
                help="every trade on the name it describes"
                current={String(eff?.[sym]?.["intent"] ?? "")}
                unsaved={pendingFor.has(sym)}
                onChange={(value) => choose(sym, value)}
              />
            ))}
            {trades.map((t) => (
              <TradeRow key={t.key} tradeKey={t.key} intent={t.intent} />
            ))}
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
                <button type="button" className="btn btn-quiet" onClick={discardIntents}>
                  Discard
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={save.isPending}
                  onClick={() =>
                    save.mutate(
                      {
                        target: "manage",
                        expectedMtime: model.targets.manage?.mtime ?? null,
                        edits: intentEdits(model),
                      },
                      {
                        onSuccess: (res) => {
                          discardIntents();
                          const note = res.backup !== null ? `Saved. Previous version backed up to ${res.backup}` : "Saved.";
                          pushToast({
                            tone: "exit",
                            title: "Intents saved",
                            message: note,
                          });
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
