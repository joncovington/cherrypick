import { Fragment, type ReactNode } from "react";
import type { BuilderChoiceWords, BuilderFieldType, BuilderWords } from "@console/shared";
import { numberProblem, showValue } from "./BuilderField";

/**
 * The module's sentences (`words.py`, the schema's `words`), read two ways: as text with each value
 * marked, for a section's one-line summary and the page's summary paragraph, and as a line of controls,
 * each sitting where its `{key}` slot is. The page words nothing itself: every word comes from the
 * template, the value's own words (`values`) or the value as written.
 */

type Obj = Record<string, unknown>;

/** A piece of a filled sentence: words, or a value the person set (marked on the page). */
export type Seg = { t: string; v?: boolean };

const SLOT = /\{([a-z0-9_.]+)\}/g;

/** A template cut into its words and its slots, in order. */
export function cut(template: string): Array<{ text: string } | { slot: string }> {
  const out: Array<{ text: string } | { slot: string }> = [];
  let at = 0;
  for (const m of template.matchAll(SLOT)) {
    if (m.index! > at) out.push({ text: template.slice(at, m.index) });
    out.push({ slot: m[1]! });
    at = m.index! + m[0].length;
  }
  if (at < template.length) out.push({ text: template.slice(at) });
  return out;
}

/** The value at a dotted path (`window.open`) of an object. */
export function at(o: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((x, k) => (typeof x === "object" && x !== null && !Array.isArray(x) ? (x as Obj)[k] : undefined), o);
}

/** `o` with the value at a dotted path set, or removed when undefined; the objects on the way are kept. */
export function setAt(o: Obj, path: string, value: unknown): Obj {
  const [head, ...rest] = path.split(".");
  const out = { ...o };
  if (rest.length === 0) {
    if (value === undefined) delete out[head!];
    else out[head!] = value;
    return out;
  }
  const inner = setAt(typeof o[head!] === "object" && o[head!] !== null ? (o[head!] as Obj) : {}, rest.join("."), value);
  if (Object.keys(inner).length === 0) delete out[head!];
  else out[head!] = inner;
  return out;
}

/** A template without a leading phrase the line already shows beside it (its lead, its kind's name):
 *  "Enter at {at} ET" beside the lead "Enter" reads "at {at} ET". Case and spacing are ignored. */
export function without(template: string, ...shown: string[]): string {
  let t = template;
  for (const s of shown) {
    const word = s.trim();
    if (!word) continue;
    // A phrase ending in a letter must end at a word's end ("at" is not the start of "at least").
    const end = /\w$/.test(word) ? "\\b" : "";
    const re = new RegExp(`^\\s*${word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}${end}\\s*`, "i");
    t = t.replace(re, "");
  }
  return t;
}

/** How one value reads: a flag's or an enum's own words, a nullable key's "null" or its "n" phrase with
 *  the number in it, a list joined with "and", else the value as written. */
export function valueWords(w: BuilderChoiceWords | undefined, key: string, v: unknown): string {
  const own = w?.values?.[key];
  if (v === null && own?.["null"]) return own["null"];
  if (typeof v === "boolean") return own?.[String(v)] ?? (v ? "yes" : "no");
  if (typeof v === "number" && own?.[String(v)]) return own[String(v)]!;
  if (typeof v === "number" && own?.["n"]) return own["n"].replace(`{${key}}`, String(v));
  if (Array.isArray(v)) return v.map((x) => valueWords(w, key, x)).join(", ");
  if (typeof v === "string") return own?.[v] ?? v.replaceAll("_", " ");
  return showValue(v);
}

/** A filter's `{range}` as its words for the bounds set, or null with neither. */
export function rangeTemplate(words: BuilderWords["range"], o: Obj): string | null {
  const lo = o["min"] !== undefined && o["min"] !== null;
  const hi = o["max"] !== undefined && o["max"] !== null;
  return lo && hi ? words.both : lo ? words.min : hi ? words.max : null;
}

/** A sentence filled from `o`: each slot's value in its words, marked. `nested` fills a slot that is a
 *  sentence of its own (a trigger's signal, an indicator); `dflt` gives a key's default where it has
 *  one. A slot with no value reads "…", marked, so a gap shows. */
export function say(
  template: string,
  o: Obj,
  w: BuilderChoiceWords | undefined,
  { nested, dflt, range }: { nested?: (slot: string, v: unknown) => Seg[] | null; dflt?: (slot: string) => unknown; range?: BuilderWords["range"] } = {},
): Seg[] {
  const out: Seg[] = [];
  for (const p of cut(template)) {
    if ("text" in p) {
      out.push({ t: p.text });
      continue;
    }
    if (p.slot === "range" && range) {
      const r = rangeTemplate(range, o);
      out.push(...(r ? say(r, o, w) : [{ t: "…", v: true }]));
      continue;
    }
    const raw = at(o, p.slot);
    const v = raw === undefined ? dflt?.(p.slot) : raw;
    const inner = nested?.(p.slot, v);
    if (inner) out.push(...inner);
    else out.push({ t: v === undefined ? "…" : valueWords(w, p.slot.split(".").pop()!, v), v: true });
  }
  return out;
}

/** Segments as plain text. */
export function plain(segs: Seg[]): string {
  return segs.map((s) => s.t).join("");
}

/** Segments drawn with each value marked. */
export function Marked({ segs }: { segs: Seg[] }) {
  return (
    <>
      {segs.map((s, i) =>
        s.v ? (
          <span key={i} className="said">
            {s.t}
          </span>
        ) : (
          <Fragment key={i}>{s.t}</Fragment>
        ),
      )}
    </>
  );
}

/** A sentence as a line of controls: its words as text, each slot the control `control` draws there. */
export function Line({ template, control }: { template: string; control: (slot: string) => ReactNode }) {
  return (
    <>
      {cut(template).map((p, i) =>
        "text" in p ? (
          p.text.trim() ? (
            <span key={i} className="word">
              {p.text.trim()}
            </span>
          ) : null
        ) : (
          <Fragment key={i}>{control(p.slot)}</Fragment>
        ),
      )}
    </>
  );
}

/** One value's control, inline in a sentence and drawn by its type from the module: a choice of the
 *  engine's words (each in the template's own words), a flag, a time, a bounded number, a leg, or text.
 *  Blank removes the key; a default shows greyed and is never written. */
export function Slot({
  id,
  label,
  t,
  value,
  onChange,
  w,
  wordKey,
  legs = [],
  legLabels = {},
  width,
}: {
  id: string;
  /** What the control is, for a reader that cannot see the line around it. */
  label: string;
  t: BuilderFieldType | undefined;
  value: unknown;
  onChange: (v: unknown) => void;
  /** The choice's words, for an option's or a flag's own words. */
  w?: BuilderChoiceWords | undefined;
  wordKey?: string;
  legs?: string[];
  legLabels?: Record<string, string>;
  width?: number;
}) {
  const key = wordKey ?? label;
  const dflt = t?.default;
  if (t?.type === "enum" || t?.type === "leg") {
    const opts = t.type === "enum" ? (t.options ?? []) : legs;
    // Unset, a key with a default shows its default chosen; choosing the default writes nothing.
    const cur = value === undefined || value === null ? (dflt !== undefined ? String(dflt) : "") : String(value);
    return (
      <select
        id={id}
        className="ctl"
        aria-label={label}
        value={cur}
        onChange={(e) => {
          const v = e.currentTarget.value;
          onChange(v === "" || (dflt !== undefined && v === String(dflt)) ? undefined : v);
        }}
      >
        {cur === "" && <option value="">choose…</option>}
        {cur !== "" && !opts.includes(cur) && <option value={cur}>{cur}</option>}
        {opts.map((o) => (
          <option key={o} value={o}>
            {t.type === "leg" ? (legLabels[o] ?? o) : valueWords(w, key, o)}
          </option>
        ))}
      </select>
    );
  }
  if (t?.type === "bool") {
    const on = value === undefined ? dflt === true : value === true;
    // A required flag is written either way; one with a default is written only where it differs; any
    // other is written only when set, since the engine reads it absent as false.
    const write = (c: boolean): unknown => (t.required ? c : dflt !== undefined ? (c === dflt ? undefined : c) : c ? true : undefined);
    return (
      <label className="opt-flag">
        <input id={id} type="checkbox" checked={on} onChange={(e) => onChange(write(e.currentTarget.checked))} /> {valueWords(w, key, true)}
      </label>
    );
  }
  if (t?.type === "time") {
    return (
      <input
        id={id}
        key={`${id}-${showValue(value)}`}
        className="ctl time"
        type="time"
        aria-label={label}
        defaultValue={typeof value === "string" ? value : ""}
        onBlur={(e) => onChange(e.currentTarget.value === "" ? undefined : e.currentTarget.value.slice(0, 5))}
      />
    );
  }
  if (t?.type === "int" || t?.type === "number") {
    const words = (t.or?.length ?? 0) > 0;
    return (
      <input
        id={id}
        key={`${id}-${showValue(value)}`}
        className="ctl"
        style={width ? { width } : undefined}
        type={words ? "text" : "number"}
        inputMode={t.type === "int" ? "numeric" : "decimal"}
        step={words ? undefined : t.type === "int" ? 1 : "any"}
        min={words ? undefined : t.min}
        max={words ? undefined : t.max}
        aria-label={label}
        placeholder={dflt !== undefined ? showValue(dflt) : undefined}
        defaultValue={showValue(value)}
        onInput={(e) => e.currentTarget.setCustomValidity(numberProblem(t, e.currentTarget.value) ?? "")}
        onBlur={(e) => {
          const el = e.currentTarget;
          const problem = el.validity.badInput ? (t.type === "int" ? "a whole number" : "a number") : numberProblem(t, el.value);
          el.setCustomValidity(problem ?? "");
          if (problem) return void el.reportValidity();
          const s = el.value.trim();
          onChange(s === "" ? undefined : t.or?.includes(s) ? s : Number(s));
        }}
      />
    );
  }
  return (
    <input
      id={id}
      key={`${id}-${showValue(value)}`}
      className="ctl"
      style={{ width: width ?? 96 }}
      aria-label={label}
      placeholder={dflt !== undefined ? showValue(dflt) : undefined}
      defaultValue={showValue(value)}
      onBlur={(e) => {
        const s = e.currentTarget.value.trim();
        if (t?.type === "json" && s !== "") {
          try {
            e.currentTarget.setCustomValidity("");
            return onChange(JSON.parse(s) as unknown);
          } catch {
            e.currentTarget.setCustomValidity("JSON: an object, a list or a number");
            return void e.currentTarget.reportValidity();
          }
        }
        onChange(s === "" ? undefined : s);
      }}
    />
  );
}
