import { useState } from "react";
import type { ConfigModelPayload } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection } from "../../components/CardHelp";
import { pushToast } from "../../lib/toast";
import { INTENTS, INTENT_WORDS, POSITIONS, effectivePositions, intentWords, pendingIntents, savedPositions, stageIntent } from "./intents";
import { unstageEdit, useStagedVersion } from "./stagedStore";
import { ConfigError, useSaveSection } from "./useConfigModel";

/**
 * Config › Position rules › Intents (2026-11-12): what each name is meant to be, read by the
 * evaluation before it judges (a wheel name's tested put may be assigned; a pmcc name's diagonal is
 * read as a PMCC). A choice is staged here or from Live → wheel and saved here, through the
 * orchestrator's config editor with its backup, as the whole `positions` object.
 */
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
  // Every name with an intent, and any being changed (one set back to none stays until saved).
  const rows = [
    ...new Set([
      ...Object.entries(eff ?? {})
        .filter(([sym, entry]) => !sym.startsWith("_") && typeof entry?.["intent"] === "string")
        .map(([sym]) => sym),
      ...pendingFor,
    ]),
  ].sort();
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
                wheel: a tested short put on the name may be assigned, so it is not stopped or closed as breached. not a wheel: the put keeps
                its stop, and the wheel tab stops asking. pmcc and diagonal decide whether a call diagonal is read as a PMCC.
              </p>
            </HelpSection>
            <HelpSection title="Where it is kept">
              <p>
                <code>positions.&lt;NAME&gt;.intent</code> in <code>~/.cherrypick/config/manage.json</code>. A choice made on Live → wheel waits
                here until you save.
              </p>
            </HelpSection>
          </>
        ),
      }}
    >
      {saved === null ? (
        <p className="muted">manage.json has no positions block, so an intent cannot be added from here.</p>
      ) : (
        <>
          <div className="cfg-fields">
            {rows.length === 0 && <p className="muted">No name has an intent yet.</p>}
            {rows.map((sym) => (
              <div className="cfg-row" key={sym}>
                <div className="cfg-row-label">
                  <label htmlFor={`intent-${sym}`}>
                    <span className="num">{sym}</span>
                    {pendingFor.has(sym) && <span className="chip chip-warn"> unsaved</span>}
                  </label>
                </div>
                <div className="cfg-row-control">
                  <select id={`intent-${sym}`} value={String(eff?.[sym]?.["intent"] ?? "")} onChange={(e) => choose(sym, e.target.value)}>
                    <option value="">none</option>
                    {INTENTS.map((i) => (
                      <option key={i} value={i}>
                        {INTENT_WORDS[i]}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            ))}
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
            {why !== null && <p className="cfg-save-error">{why}</p>}
          </div>
          {pending.length > 0 && (
            <div className="cfg-savebar">
              <div className="cfg-savebar-changes">
                <strong>
                  {pending.length} unsaved intent{pending.length === 1 ? "" : "s"}
                </strong>
                <ul>
                  {pending.map((p) => (
                    <li key={p.symbol}>
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
                <button type="button" className="btn btn-quiet" onClick={() => unstageEdit("manage", POSITIONS)}>
                  Discard
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={save.isPending}
                  onClick={() =>
                    save.mutate(
                      { target: "manage", expectedMtime: model.targets.manage?.mtime ?? null, edits: [{ pointer: POSITIONS, value: eff }] },
                      {
                        onSuccess: (res) => {
                          unstageEdit("manage", POSITIONS);
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
