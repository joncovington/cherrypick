import { useState } from "react";
import type { ConfigModelPayload, ConfigTargetId } from "@console/shared";
import { Card } from "../../components/DataTable";
import { FieldRow } from "./FieldRow";
import { resolveSection, TARGET_TITLES, type SectionMeta } from "./fieldMeta";
import { clearStaged, getStaged, sameValue, useStagedVersion } from "./stagedStore";
import { ConfigError, useSaveSection } from "./useConfigModel";
import { pushToast } from "../../lib/toast";

/**
 * One group of settings, saved as a unit.
 *
 * Explicit save rather than save-on-change: these values steer loops that trade, and a stray
 * keystroke should be something you can look at before it lands. The bar lists exactly what will
 * change, and each target saves as one atomic write with one backup on the server side.
 */

function describe(value: unknown): string {
  if (Array.isArray(value)) return value.length === 0 ? "(empty)" : value.join(", ");
  if (typeof value === "string") return value === "" ? '""' : value;
  return String(value);
}

function SaveBar({
  target,
  changes,
  expectedMtime,
}: {
  target: ConfigTargetId;
  changes: Array<{ pointer: string; from: unknown; to: unknown }>;
  expectedMtime: number | null;
}) {
  const save = useSaveSection();
  const [note, setNote] = useState<string | null>(null);

  const err = save.error as ConfigError | Error | null;
  const conflict = err instanceof ConfigError && err.code === "conflict";

  return (
    <div className="cfg-savebar">
      <div className="cfg-savebar-changes">
        <strong>
          {changes.length} unsaved change{changes.length === 1 ? "" : "s"} in {TARGET_TITLES[target]}
        </strong>
        <ul>
          {changes.map((c) => (
            <li key={c.pointer}>
              <code>{c.pointer}</code> {describe(c.from)} → <strong>{describe(c.to)}</strong>
            </li>
          ))}
        </ul>
        {note !== null && <p className="cfg-saved-note muted">{note}</p>}
        {err !== null && (
          <p className="cfg-save-error">
            {conflict
              ? "This file changed on disk since the page loaded — it has been reloaded. Check your edits and save again."
              : err.message}
          </p>
        )}
      </div>
      <div className="cfg-savebar-actions">
        <button
          type="button"
          className="btn btn-quiet"
          onClick={() => {
            clearStaged(changes.map((c) => ({ target, pointer: c.pointer })));
            setNote(null);
          }}
        >
          Discard
        </button>
        <button
          type="button"
          className="btn"
          disabled={save.isPending}
          onClick={() => {
            save.mutate(
              { target, expectedMtime, edits: changes.map((c) => ({ pointer: c.pointer, value: c.to })) },
              {
                onSuccess: (res) => {
                  clearStaged(changes.map((c) => ({ target, pointer: c.pointer })));
                  const noteText = res.backup !== null ? `Saved. Previous version backed up to ${res.backup}` : "Saved.";
                  setNote(noteText);
                  // The inline note above vanishes the instant this SaveBar unmounts (changes
                  // drops to 0 right after a successful save), which can happen too fast to
                  // register -- the toast is the durable confirmation.
                  pushToast({ tone: "exit", title: `${TARGET_TITLES[target]} saved`, message: noteText });
                },
              },
            );
          }}
        >
          {save.isPending ? "saving…" : `Save ${TARGET_TITLES[target]}`}
        </button>
      </div>
    </div>
  );
}

export function ConfigSection({
  section,
  model,
  updatedAt,
  meicRiskDirty,
}: {
  section: SectionMeta;
  model: ConfigModelPayload | undefined;
  updatedAt?: number;
  /** Whether the repo-tracked meic risk file currently has uncommitted changes. */
  meicRiskDirty?: boolean | null;
}) {
  useStagedVersion();
  const groups = resolveSection(section.id, model?.targets);
  if (groups.length === 0) return null;

  return (
    <Card title={section.title} collapseKey={`config-${section.id}`} updatedAt={updatedAt}>
      <p className="cfg-section-blurb muted">{section.blurb}</p>
      {groups.map((group) => {
        const targetModel = model?.targets[group.target];
        const changes = group.fields
          .map((f) => {
            const { has, value } = getStaged(group.target, f.pointer);
            return has && !sameValue(value, f.value) ? { pointer: f.pointer, from: f.value, to: value } : null;
          })
          .filter((c): c is { pointer: string; from: unknown; to: unknown } => c !== null);

        return (
          <div className="cfg-group" key={group.target}>
            <div className="cfg-group-head">
              <span className="cfg-group-title">{TARGET_TITLES[group.target]}</span>
              {group.target === "meic-risk" && (
                <span className="cfg-group-note">
                  lives in the repo (<code>packages/meic/config.risk.json</code>), so a save changes your working
                  tree
                  {meicRiskDirty === true && <strong> — uncommitted changes there now</strong>}
                </span>
              )}
            </div>
            <div className="cfg-fields">
              {group.fields.map((f) => (
                <FieldRow key={f.pointer} target={group.target} field={f} />
              ))}
            </div>
            {changes.length > 0 && (
              <SaveBar target={group.target} changes={changes} expectedMtime={targetModel?.mtime ?? null} />
            )}
          </div>
        );
      })}
    </Card>
  );
}
