import type { BuilderFieldType } from "@console/shared";

/**
 * One field of the Builder's form, drawn by its type from the module (`fieldtypes`): a whole or decimal
 * number box bounded as the engine bounds it, a time picker, a choice of the engine's words, a choice of
 * the legs placed before this one, a checkbox, or text (JSON for an object). A value the type refuses is
 * not written: the box is marked and says why. A default is the engine's, shown greyed in the empty field
 * and never written, so leaving it blank leaves the block as it was backtested. Without a type (an older
 * module) a field is the text box it always was.
 */

export interface FieldProps {
  id: string;
  label: string;
  value: unknown;
  onChange: (v: unknown) => void;
  required?: boolean;
  t?: BuilderFieldType | undefined;
  /** The legs placed before this one, for a `leg` field. */
  legs?: string[];
  /** What a blank means where it is not the engine's default (a trigger's `within`: until it closes). */
  blankMeans?: string;
}

/** A field's text as the spec's value: blank is absent, then a number, a flag, JSON, or text. */
export function parseValue(text: string): unknown {
  const t = text.trim();
  if (t === "") return undefined;
  if (t === "true") return true;
  if (t === "false") return false;
  if (t === "null") return null;
  if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t);
  if (t.startsWith("{") || t.startsWith("[") || t.startsWith('"')) {
    try {
      return JSON.parse(t) as unknown;
    } catch {
      return t;
    }
  }
  return t;
}

/** A spec value as its field shows it. */
export function showValue(v: unknown): string {
  if (v === undefined || v === null) return "";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function word(v: unknown): string {
  return v === true ? "yes" : v === false ? "no" : showValue(v).replaceAll("_", " ");
}

/** The words a number field says when its text is refused, or null when the text is a value it takes
 *  (blank included). */
export function numberProblem(t: BuilderFieldType, text: string): string | null {
  const s = text.trim();
  if (s === "") return null;
  if (t.or?.includes(s)) return null;
  const n = Number(s);
  const whole = t.type === "int";
  const words = t.or?.length ? ` or ${t.or.join(", ")}` : "";
  if (!Number.isFinite(n) || (whole && !Number.isInteger(n))) return `${whole ? "a whole number" : "a number"}${words}`;
  if (t.above !== undefined && n <= t.above) return `more than ${t.above}`;
  if (t.min !== undefined && n < t.min) return `at least ${t.min}`;
  if (t.max !== undefined && n > t.max) return `at most ${t.max}`;
  return null;
}

/** The hint under a number's label: its bounds in words. */
export function boundText(t: BuilderFieldType): string {
  const parts: string[] = [];
  if (t.type === "int") parts.push("whole");
  if (t.above !== undefined) parts.push(`> ${t.above}`);
  if (t.min !== undefined && !(t.max !== undefined && t.min === t.max)) parts.push(`≥ ${t.min}`);
  if (t.max !== undefined) parts.push(t.min === t.max ? `= ${t.max}` : `≤ ${t.max}`);
  if (t.or?.length) parts.push(`or ${t.or.join(", ")}`);
  return parts.join(", ");
}

function Label({ id, label, required, hint }: { id: string; label: string; required?: boolean | undefined; hint?: string }) {
  return (
    <label className="builder-lbl" htmlFor={id}>
      {label.replaceAll("_", " ")}
      {required ? <span className="builder-req"> *</span> : null}
      {hint ? <span className="builder-hint"> · {hint}</span> : null}
    </label>
  );
}

export function TypedField({ id, label, value, onChange, required, t, legs = [], blankMeans }: FieldProps) {
  const dflt = t?.default;
  const placeholder = dflt !== undefined ? `default ${word(dflt)}` : blankMeans;
  if (t?.type === "enum" || t?.type === "leg" || (t?.type === "bool" && dflt !== undefined)) {
    const opts = t.type === "enum" ? (t.options ?? []) : t.type === "leg" ? legs : ["true", "false"];
    const cur = value === undefined || value === null ? "" : String(value);
    const blank = dflt !== undefined ? `default (${word(dflt)})` : required ? "choose…" : "none";
    return (
      <div className="builder-f">
        <Label id={id} label={label} required={required || undefined} />
        <select
          id={id}
          className="builder-input"
          value={cur}
          required={required || undefined}
          aria-required={required || undefined}
          onChange={(e) => {
            const v = e.currentTarget.value;
            onChange(v === "" ? undefined : t.type === "bool" ? v === "true" : v);
          }}
        >
          <option value="">{blank}</option>
          {cur !== "" && !opts.includes(cur) && <option value={cur}>{cur}</option>}
          {opts.map((o) => (
            <option key={o} value={o}>
              {t.type === "bool" ? word(o === "true") : t.type === "leg" ? o : o.replaceAll("_", " ")}
            </option>
          ))}
        </select>
      </div>
    );
  }
  if (t?.type === "bool") {
    return (
      <div className="builder-f">
        <Label id={id} label={label} />
        <input id={id} className="builder-flag" type="checkbox" checked={value === true} onChange={(e) => onChange(e.currentTarget.checked ? true : undefined)} />
      </div>
    );
  }
  if (t?.type === "time") {
    return (
      <div className="builder-f">
        <Label id={id} label={label} required={required || undefined} hint="ET" />
        <input
          id={id}
          className="builder-input"
          type="time"
          defaultValue={typeof value === "string" ? value : ""}
          required={required || undefined}
          aria-required={required || undefined}
          onBlur={(e) => onChange(e.currentTarget.value === "" ? undefined : e.currentTarget.value.slice(0, 5))}
        />
      </div>
    );
  }
  if (t?.type === "int" || t?.type === "number") {
    const words = (t.or?.length ?? 0) > 0;
    return (
      <div className="builder-f">
        <Label id={id} label={label} required={required || undefined} hint={boundText(t)} />
        <input
          id={id}
          className="builder-input"
          type={words ? "text" : "number"}
          inputMode={t.type === "int" ? "numeric" : "decimal"}
          step={words ? undefined : t.type === "int" ? 1 : "any"}
          min={words ? undefined : t.min}
          max={words ? undefined : t.max}
          placeholder={placeholder}
          defaultValue={showValue(value)}
          required={required || undefined}
          aria-required={required || undefined}
          onInput={(e) => {
            const el = e.currentTarget;
            el.setCustomValidity(numberProblem(t, el.value) ?? "");
          }}
          onBlur={(e) => {
            const el = e.currentTarget;
            const problem = el.validity.badInput ? (t.type === "int" ? "a whole number" : "a number") : numberProblem(t, el.value);
            el.setCustomValidity(problem ?? "");
            if (problem) {
              el.reportValidity();
              return;
            }
            const s = el.value.trim();
            onChange(s === "" ? undefined : t.or?.includes(s) ? s : Number(s));
          }}
        />
      </div>
    );
  }
  const asText = t?.type === "text";
  return (
    <div className="builder-f">
      <Label id={id} label={label} required={required || undefined} hint={t?.type === "json" ? "JSON" : undefined} />
      <input
        id={id}
        className="builder-input"
        placeholder={placeholder}
        defaultValue={showValue(value)}
        required={required || undefined}
        aria-required={required || undefined}
        onBlur={(e) => {
          const s = e.currentTarget.value;
          if (asText) return onChange(s.trim() === "" ? undefined : s.trim());
          if (t?.type === "json" && s.trim() !== "") {
            try {
              e.currentTarget.setCustomValidity("");
              return onChange(JSON.parse(s) as unknown);
            } catch {
              e.currentTarget.setCustomValidity("JSON: an object, a list or a number");
              e.currentTarget.reportValidity();
              return;
            }
          }
          onChange(parseValue(s));
        }}
      />
    </div>
  );
}
