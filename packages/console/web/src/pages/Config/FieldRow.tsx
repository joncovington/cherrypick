import type { ConfigTargetId } from "@console/shared";
import type { ResolvedField } from "./fieldMeta";
import { getStaged, sameValue, stageEdit, unstageEdit, useStagedVersion } from "./stagedStore";

/**
 * One editable setting.
 *
 * Guarded pointers render here too, as a locked row rather than a hidden one — a live switch you
 * cannot see is a live switch you go looking for somewhere less careful. The hint beside it is the
 * config editor's own text, naming the real path.
 *
 * Help sits inline under the label rather than behind a hover: this is a form, and something you
 * need in order to decide what to type should not require finding it first.
 */

function ListInput({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <input
      className="text-input"
      value={value.join(", ")}
      spellCheck={false}
      onChange={(e) =>
        onChange(
          e.target.value
            .split(",")
            .map((s) => s.trim())
            .filter((s) => s !== ""),
        )
      }
    />
  );
}

export function FieldRow({ target, field }: { target: ConfigTargetId; field: ResolvedField }) {
  useStagedVersion();
  const { has, value: stagedValue } = getStaged(target, field.pointer);
  const current = has ? stagedValue : field.value;
  const dirty = has && !sameValue(stagedValue, field.value);

  const set = (v: unknown) => {
    // Staging a value that matches the file is not an edit — clear it so the section stops
    // claiming a change it no longer has.
    if (sameValue(v, field.value)) unstageEdit(target, field.pointer);
    else stageEdit(target, field.pointer, v);
  };

  const { type, options, min, max, step } = field.meta;
  const id = `cfg-${target}-${field.pointer}`;

  return (
    <div className={`cfg-row ${dirty ? "cfg-row-dirty" : ""} ${field.guardedHint !== null ? "cfg-row-locked" : ""}`}>
      <div className="cfg-row-label">
        <label htmlFor={id}>
          {field.label}
          {dirty && <span className="cfg-dirty-dot" title="unsaved" />}
        </label>
        <code className="cfg-pointer">{field.pointer}</code>
        {field.help !== null && <p className="cfg-help">{field.help}</p>}
      </div>

      <div className="cfg-row-control">
        {field.guardedHint !== null ? (
          <div className="cfg-locked">
            <span className="chip chip-warn" title={field.guardedHint}>
              🔒 {String(field.value)}
            </span>
            <span className="cfg-help">{field.guardedHint}</span>
          </div>
        ) : type === "boolean" ? (
          <div className="mode-toggle" role="group" aria-label={field.label}>
            <button
              type="button"
              className={`mode-btn ${current === false ? "active" : ""}`}
              onClick={() => set(false)}
            >
              off
            </button>
            <button
              type="button"
              className={`mode-btn ${current === true ? "active" : ""}`}
              onClick={() => set(true)}
            >
              on
            </button>
          </div>
        ) : type === "enum" ? (
          <select id={id} className="text-input" value={String(current)} onChange={(e) => set(e.target.value)}>
            {(options ?? []).map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        ) : type === "number" ? (
          <input
            id={id}
            className="num-input"
            type="number"
            value={typeof current === "number" ? current : ""}
            min={min}
            max={max}
            step={step ?? 1}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (e.target.value !== "" && Number.isFinite(n)) set(n);
            }}
          />
        ) : type === "stringList" ? (
          <ListInput value={Array.isArray(current) ? current.map(String) : []} onChange={set} />
        ) : (
          // time and string share the control — an HH:MM field with a stray character is worse
          // than a plain text field, since the config's own format is the only authority.
          <input
            id={id}
            className="text-input"
            value={typeof current === "string" ? current : ""}
            spellCheck={false}
            placeholder={type === "time" ? "HH:MM" : undefined}
            onChange={(e) => set(e.target.value)}
          />
        )}

        {dirty && (
          <button type="button" className="btn btn-quiet cfg-revert" onClick={() => unstageEdit(target, field.pointer)}>
            revert
          </button>
        )}
      </div>
    </div>
  );
}
