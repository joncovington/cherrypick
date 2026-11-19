import { useRef, useState } from "react";
import type { BuilderBlock, BuilderFieldType, BuilderSchema } from "@console/shared";
import { TypedField, showValue, type FieldProps } from "./BuilderField";

export { parseValue, showValue } from "./BuilderField";
import { CardHelpButton } from "../../components/CardHelp";
import { SECTION_HELP } from "./BuilderHelp";
import { TradeShape } from "./TradeShape";
import { StrategyPicker } from "./StrategyPicker";
import { isLegId, legLabels, legWords, nextLegId, openVerb, signedQty, titleCase, writtenLeg } from "./legWords";

/**
 * The Builder's form (#1180): the five sections of one automation, each field writing the same spec
 * the JSON editor holds and the paper loop reads. Every choice comes from the module's schema
 * (`cli builder-schema`, the engine's own tables), so the form offers nothing the engine would
 * refuse by name; whether a value is right is the module's Check. A value is typed as the field
 * shows it: a number, true or false, JSON for a list or an object, otherwise text. A blank field
 * removes its key.
 */

export type Spec = Record<string, unknown>;
type Obj = Record<string, unknown>;

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];
const TEXT: BuilderFieldType = { type: "text" };

const STATE_TEXT: Record<BuilderBlock["state"], string> = {
  backtested: "backtested",
  changed: "changed since its backtest",
  not_backtested: "not backtested",
};
const STATE_CHIP: Record<BuilderBlock["state"], string> = {
  backtested: "chip chip-ok",
  changed: "chip chip-warn",
  not_backtested: "chip",
};

function obj(v: unknown): Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Obj) : {};
}

function list(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

/** `o` with `key` set to `value`, or removed when the value is undefined. */
export function withKey(o: Obj, key: string, value: unknown): Obj {
  const out = { ...o };
  if (value === undefined) delete out[key];
  else out[key] = value;
  return out;
}

/** A field, typed where the module says (`BuilderField`); keyed on its value so an outside change redraws it. */
function Field(p: FieldProps) {
  return <TypedField key={`${p.id}-${showValue(p.value)}`} {...p} />;
}

function Select({
  id,
  label,
  value,
  options,
  onChange,
  blank,
  words,
}: {
  id: string;
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  /** What an unset value means (the engine's default); offered as the first choice. */
  blank?: string;
  /** Each option's words where they are not the option itself ("P" reads "Put"). */
  words?: Record<string, string>;
}) {
  return (
    <div className="builder-f">
      <label className="builder-lbl" htmlFor={id}>
        {label}
      </label>
      <select id={id} className="builder-input" value={value} onChange={(e) => onChange(e.currentTarget.value)}>
        {blank !== undefined && <option value="">{blank}</option>}
        {!options.includes(value) && !(blank !== undefined && value === "") && <option value={value}>{value || "choose…"}</option>}
        {options.map((o) => (
          <option key={o} value={o}>
            {words?.[o] ?? o.replaceAll("_", " ")}
          </option>
        ))}
      </select>
    </div>
  );
}

/** A true-or-false key: checked sets it, unchecked removes it (the engine reads absent as false). */
function Flag({ id, label, value, onChange }: { id: string; label: string; value: unknown; onChange: (v: true | undefined) => void }) {
  return (
    <div className="builder-f">
      <label className="builder-lbl" htmlFor={id}>
        {label.replaceAll("_", " ")}
      </label>
      <input id={id} className="builder-flag" type="checkbox" checked={value === true} onChange={(e) => onChange(e.currentTarget.checked ? true : undefined)} />
    </div>
  );
}

/** The keys a kind takes ({key: required}), each a field on `o` drawn by its type in `types`; without
 *  types (an older module) a key in `flags` is a checkbox and the rest text. */
function KeyFields({
  prefix,
  keys,
  o,
  onChange,
  skip = [],
  flags = [],
  types,
  legs,
  legLabels,
}: {
  prefix: string;
  keys: Record<string, boolean>;
  o: Obj;
  onChange: (next: Obj) => void;
  skip?: string[];
  flags?: string[];
  types?: Record<string, BuilderFieldType> | undefined;
  legs?: string[];
  legLabels?: Record<string, string>;
}) {
  return (
    <>
      {Object.entries(keys)
        .filter(([k]) => !skip.includes(k))
        .map(([k, req]) =>
          !types && flags.includes(k) ? (
            <Flag key={`${prefix}-${k}`} id={`${prefix}-${k}`} label={k} value={o[k]} onChange={(v) => onChange(withKey(o, k, v))} />
          ) : (
            <Field
              key={`${prefix}-${k}`}
              id={`${prefix}-${k}`}
              label={k}
              value={o[k]}
              required={req}
              t={types?.[k]}
              legs={legs}
              legLabels={legLabels}
              onChange={(v) => onChange(withKey(o, k, v))}
            />
          ),
        )}
    </>
  );
}

function SectionHead({
  n,
  title,
  blocks,
  labels,
  note,
  help,
}: {
  n: number;
  title: string;
  blocks: string[];
  labels: Record<string, BuilderBlock["state"]>;
  note?: string;
  help: keyof typeof SECTION_HELP;
}) {
  return (
    <div className="builder-section-head">
      <h4>
        {n} · {title}
      </h4>
      <CardHelpButton cardTitle={`${n} · ${title}`} help={SECTION_HELP[help]!} />
      {blocks.map((b) => {
        const s = labels[b] ?? "not_backtested";
        return (
          <span key={b} className={STATE_CHIP[s]}>
            {b} · {STATE_TEXT[s]}
          </span>
        );
      })}
      {note && <span className="muted builder-section-note">{note}</span>}
    </div>
  );
}

/** One sentence for the entry, from its own fields. */
export function entryText(entry: Obj): string {
  const kind = entry["kind"];
  if (kind === "time") return `Enter at ${showValue(entry["at"])}, for ${showValue(entry["window"] ?? 5)} minutes.`;
  if (kind === "orb") {
    return `Enter on the first break ${showValue(entry["direction"])} the ${showValue(entry["minutes"])}-minute opening range, until ${showValue(entry["latest"])}.`;
  }
  if (kind === "trigger") {
    const t = obj(entry["trigger"]);
    if ("at" in t) return `Enter at ${showValue(t["at"])}, for ${showValue(t["within"])} minutes.`;
    const sig = obj(t["signal"]);
    const params = Object.entries(sig)
      .filter(([k]) => k !== "kind")
      .map(([k, v]) => `${k} ${showValue(v)}`)
      .join(", ");
    const w = obj(t["window"]);
    const bars = entry["bar_minutes"] ? ` on ${showValue(entry["bar_minutes"])}-minute bars` : "";
    const daily = t["daily"] ? " on the last completed daily bar" : bars;
    return `Enter on the first ${showValue(sig["kind"] ?? "signal")} firing (${params || "no settings"})${daily}, between ${showValue(w["open"])} and ${showValue(w["close"])}.`;
  }
  return "Choose how it enters.";
}

function GateRows({ schema, gates, stages, onChange, prefix }: { schema: BuilderSchema; gates: Obj[]; stages: string[]; onChange: (next: Obj[]) => void; prefix: string }) {
  const known = schema.gates.filter((g) => stages.includes(g.stage));
  const byName = Object.fromEntries(schema.gates.map((g) => [g.gate, g]));
  const shown = gates.map((g, i) => ({ g, i })).filter(({ g }) => stages.includes(byName[String(g["gate"])]?.stage ?? stages[0]!));
  return (
    <>
      <div className="builder-rows">
      {shown.map(({ g, i }) => {
        const def = byName[String(g["gate"])];
        return (
          <div key={`${prefix}-${i}-${String(g["gate"])}`} className="builder-cond builder-gate-row">
            <Select
              id={`${prefix}-${i}`}
              label="gate"
              value={String(g["gate"] ?? "")}
              options={known.map((k) => k.gate)}
              onChange={(name) => onChange(gates.map((x, j) => (j === i ? { gate: name } : x)))}
            />
            {(def?.params ?? []).map((p) => {
              const t = schema.types?.gates[String(g["gate"])]?.[p.name];
              return (
                <Field
                  key={`${prefix}-${i}-${p.name}`}
                  id={`${prefix}-${i}-${p.name}`}
                  label={t || p.default === null || p.default === undefined ? p.name : `${p.name} (${showValue(p.default)})`}
                  value={g[p.name]}
                  required={p.required}
                  t={t}
                  onChange={(v) => onChange(gates.map((x, j) => (j === i ? withKey(x, p.name, v) : x)))}
                />
              );
            })}
            <button type="button" className="btn builder-remove" aria-label={`Remove gate ${String(g["gate"])}`} onClick={() => onChange(gates.filter((_, j) => j !== i))}>
              ×
            </button>
          </div>
        );
      })}
      </div>
      <div>
        <button type="button" className="btn" onClick={() => onChange([...gates, { gate: known[0]?.gate ?? "" }])}>
          + gate
        </button>
      </div>
    </>
  );
}

/** What every section's fields take: the schema, the whole spec and how to change it. */
export interface SectionProps {
  schema: BuilderSchema;
  spec: Spec;
  onChange: (next: Spec) => void;
}

/** Section 1's fields: symbol, expiry rule, weekdays, event skips. Read-only, every field is shown and
 *  none changes; the event key still opens. */
export function UnderlyingFields({ schema, spec, onChange, readOnly = false }: SectionProps & { readOnly?: boolean }) {
  const set = (key: string, value: unknown) => onChange(withKey(spec, key, value));
  const expiry = obj(spec["expiry"]);
  const skip = obj(spec["skip_events"]);
  const labelsOn = list(skip["labels"]).map(String);
  const weekdays = list(spec["weekdays"]).map(Number);
  const exprKind = String(expiry["kind"] ?? "dte");
  return (
    <>
      <fieldset className="builder-lock" disabled={readOnly}>
      <div className="builder-grid">
        <Field id="f-symbol" label="symbol" value={spec["symbol"]} required t={TEXT} onChange={(v) => set("symbol", typeof v === "string" ? v.toUpperCase() : v)} />
        <Select id="f-expiry-kind" label="expiry" value={exprKind} options={Object.keys(schema.expiry)} onChange={(k) => set("expiry", { kind: k })} />
        <KeyFields
          prefix="f-expiry"
          keys={schema.expiry[exprKind] ?? {}}
          flags={schema.expiry_flags}
          types={schema.types?.expiry}
          o={expiry}
          onChange={(next) => set("expiry", { ...next, kind: exprKind })}
        />
      </div>
      <fieldset className="builder-days">
        <legend className="builder-lbl">weekdays</legend>
        {schema.weekdays.map((d) => (
          <label key={d}>
            <input
              type="checkbox"
              checked={weekdays.includes(d)}
              onChange={(e) => set("weekdays", (e.currentTarget.checked ? [...weekdays, d] : weekdays.filter((x) => x !== d)).sort())}
            />{" "}
            {WEEKDAYS[d]}
          </label>
        ))}
      </fieldset>
      </fieldset>
      <EventSkips
        schema={schema}
        on={labelsOn}
        readOnly={readOnly}
        onChange={(next) => set("skip_events", next.length ? { ...skip, labels: next } : undefined)}
      />
    </>
  );
}

/** An event label's name, the module's own, or the label where an older module gives none. */
export function eventName(schema: BuilderSchema, label: string): string {
  return schema.event_about?.[label]?.name ?? label;
}

function dayText(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  return `${WEEKDAYS[(d.getUTCDay() + 6) % 7] ?? ""} ${iso}`;
}

/** The event skips by name, sorted by name, with a key of what each is, when it falls and its next day
 *  (closed until its ? is pressed; `keyOpen` opens it at first). */
export function EventSkips({
  schema,
  on,
  onChange,
  keyOpen = false,
  readOnly = false,
}: {
  schema: BuilderSchema;
  on: string[];
  onChange: (next: string[]) => void;
  keyOpen?: boolean;
  /** Checkboxes shown, not changeable; the key still opens. */
  readOnly?: boolean;
}) {
  const [key, setKey] = useState(keyOpen);
  const labels = [...schema.event_labels].sort((a, b) => eventName(schema, a).localeCompare(eventName(schema, b)));
  const about = schema.event_about;
  return (
    <fieldset className="builder-days builder-events">
      <legend className="builder-lbl">
        skip on these events
        {about && (
          <button type="button" className={key ? "wiz-q is-open" : "wiz-q"} aria-label="What are these events?" aria-expanded={key} onClick={() => setKey(!key)}>
            ?
          </button>
        )}
      </legend>
      {labels.map((l) => (
        <label key={l} title={about?.[l]?.when}>
          <input type="checkbox" checked={on.includes(l)} disabled={readOnly || undefined} onChange={(e) => onChange(e.currentTarget.checked ? [...on, l] : on.filter((x) => x !== l))} />{" "}
          {eventName(schema, l)}
        </label>
      ))}
      {key && about && (
        <table className="builder-event-key" role="note">
          <thead>
            <tr>
              <th>Event</th>
              <th>When</th>
              <th>Next</th>
            </tr>
          </thead>
          <tbody>
            {labels.map((l) => (
              <tr key={l}>
                <td>{eventName(schema, l)}</td>
                <td>{about[l]?.when}</td>
                <td className="num">{about[l]?.next ? dayText(about[l]!.next!) : "—"}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={3} className="muted">
                A skipped event means no new trade that day. A day the calendar has no data for is not skipped, and the backtest marks it
                unknown. Earnings and ex-dividend are the symbol's own dates.
              </td>
            </tr>
          </tfoot>
        </table>
      )}
    </fieldset>
  );
}

/** Section 2's fields: the entry kind and its keys, and the sentence. */
export function EntryFields({ schema, spec, onChange }: SectionProps) {
  const set = (key: string, value: unknown) => onChange(withKey(spec, key, value));
  const entry = obj(spec["entry"]);
  const trigger = obj(entry["trigger"]);
  const form = "at" in trigger ? "at" : "signal";
  const signal = obj(trigger["signal"]);
  const entryKind = String(entry["kind"] ?? "time");
  const types = schema.types;
  const sigType = (p: string) => types?.signals.kinds[String(signal["kind"])]?.[p] ?? types?.signals.rules[p];
  return (
    <>
      <div className="builder-grid">
        <Select id="f-entry-kind" label="entry" value={entryKind} options={Object.keys(schema.entry)} onChange={(k) => set("entry", { kind: k })} />
        {entryKind === "orb" ? (
          <>
            <KeyFields
              prefix="f-orb"
              keys={schema.entry["orb"] ?? {}}
              types={types?.entry["orb"]}
              o={entry}
              skip={["direction"]}
              onChange={(next) => set("entry", { ...next, kind: "orb" })}
            />
            <Select id="f-orb-direction" label="direction" value={String(entry["direction"] ?? "")} options={schema.orb_directions} onChange={(v) => set("entry", { ...entry, direction: v })} />
          </>
        ) : entryKind === "trigger" ? (
          <>
            <Select
              id="f-trigger-form"
              label="fires"
              value={form}
              options={Object.keys(schema.trigger_forms)}
              onChange={(f) => set("entry", { ...entry, trigger: f === "at" ? { at: "", within: 5 } : { signal: {}, window: {}, within: null } })}
            />
            {form === "at" ? (
              <KeyFields prefix="f-at" keys={schema.trigger_forms["at"] ?? {}} types={types?.trigger} o={trigger} onChange={(next) => set("entry", { ...entry, trigger: next })} />
            ) : (
              <>
                <Select
                  id="f-signal-kind"
                  label="signal"
                  value={String(signal["kind"] ?? "")}
                  options={Object.keys(schema.signals)}
                  onChange={(k) => set("entry", { ...entry, trigger: { ...trigger, signal: { kind: k } } })}
                />
                {[...(schema.signals[String(signal["kind"])] ?? []), ...schema.signal_rules].map((p) => (
                  <Field
                    key={`f-sig-${p}`}
                    id={`f-sig-${p}`}
                    label={p}
                    value={signal[p]}
                    required={sigType(p)?.required}
                    t={sigType(p)}
                    onChange={(v) => set("entry", { ...entry, trigger: { ...trigger, signal: withKey(signal, p, v) } })}
                  />
                ))}
                {(["open", "close"] as const).map((w) => (
                  <Field
                    key={`f-win-${w}`}
                    id={`f-win-${w}`}
                    label={`window ${w}`}
                    value={obj(trigger["window"])[w]}
                    required
                    t={types?.trigger[w]}
                    onChange={(v) => set("entry", { ...entry, trigger: { ...trigger, window: withKey(obj(trigger["window"]), w, v) } })}
                  />
                ))}
                <Field
                  id="f-within"
                  label={types ? "within" : "within (blank: until it closes)"}
                  value={trigger["within"] ?? undefined}
                  t={types?.trigger["within"]}
                  blankMeans="until it closes"
                  onChange={(v) => set("entry", { ...entry, trigger: { ...trigger, within: v === undefined ? null : v } })}
                />
                <Field id="f-daily" label="daily" value={trigger["daily"]} t={types?.trigger["daily"]} onChange={(v) => set("entry", { ...entry, trigger: withKey(trigger, "daily", v) })} />
              </>
            )}
            <KeyFields
              prefix="f-trig"
              keys={schema.entry["trigger"] ?? {}}
              types={types?.entry["trigger"]}
              o={entry}
              skip={["trigger"]}
              onChange={(next) => set("entry", { ...next, kind: "trigger", trigger })}
            />
          </>
        ) : (
          <KeyFields prefix="f-time" keys={schema.entry["time"] ?? {}} types={types?.entry["time"]} o={entry} onChange={(next) => set("entry", { ...next, kind: "time" })} />
        )}
      </div>
      <div className="builder-plain">
        <span className="builder-lbl">in plain English</span>
        <p>{entryText(entry)}</p>
        <p className="muted">Read by the engine paper runs, at each tick in the window, Eastern time.</p>
      </div>
    </>
  );
}

/** The gates of the given stages, in the spec's own order. */
export function GateFields({ schema, spec, onChange, stages, prefix }: SectionProps & { stages: string[]; prefix: string }) {
  const gates = list(spec["gates"]).map(obj);
  return <GateRows schema={schema} gates={gates} stages={stages} prefix={prefix} onChange={(next) => onChange(withKey(spec, "gates", next.length ? next : undefined))} />;
}

/** The most contracts one leg may hold, either way: the module's `arms.MAX_LEG_QUANTITY`, which its
 *  check enforces; the box only stops a typo early. */
const MAX_QTY = 10;

/** Section 4's legs: each named by its ID and its words (L2 · Short Put ×2), its side and contracts,
 *  right, strike rule, its keys and match mode. `side` is stored as the signed contracts. */
export function LegFields({ schema, spec, onChange }: SectionProps) {
  const set = (key: string, value: unknown) => onChange(withKey(spec, key, value));
  const legs = list(spec["legs"]).map(obj);
  const given = useRef(0);
  const labels = legLabels(legs);
  return (
    <>
      <div className="builder-rows">
      {legs.map((leg, i) => {
        const strike = obj(leg["strike"]);
        const rule = String(strike["rule"] ?? "");
        const def = schema.strike_rules[rule];
        // Every edit writes the leg in the page's spelling (action, qty, side), whichever it was read in.
        const setLeg = (next: Obj) => set("legs", legs.map((x, j) => (j === i ? writtenLeg(next) : x)));
        const w = writtenLeg(leg);
        const side = signedQty(leg);
        const qty = Math.abs(side);
        const name = String(leg["name"] ?? "");
        return (
          <div key={`leg-${i}`} className="builder-cond builder-leg-row">
            <div className="builder-f builder-leg-name">
              <span className="builder-lbl">leg</span>
              <span className="builder-leg-id">
                {isLegId(name) && <span className="builder-leg-chip">{name}</span>}
                <span>{isLegId(name) ? legWords(leg) : titleCase(name) || legWords(leg)}</span>
              </span>
            </div>
            <Select
              id={`f-leg-${i}-action`}
              label="action"
              value={side < 0 ? "sell" : "buy"}
              options={["buy", "sell"]}
              words={{ buy: openVerb(1), sell: openVerb(-1) }}
              onChange={(v) => setLeg({ ...w, action: v === "sell" ? "STO" : "BTO" })}
            />
            <div className="builder-f">
              <label className="builder-lbl" htmlFor={`f-leg-${i}-qty`}>
                qty
              </label>
              <input
                id={`f-leg-${i}-qty`}
                key={`qty-${qty}`}
                className="builder-input builder-qty"
                type="number"
                inputMode="numeric"
                min={1}
                max={MAX_QTY}
                step={1}
                defaultValue={qty}
                onBlur={(e) => {
                  const n = Math.round(Number(e.currentTarget.value));
                  if (Number.isFinite(n) && n >= 1 && n <= MAX_QTY && n !== qty) setLeg({ ...w, qty: n });
                  else e.currentTarget.value = String(qty);
                }}
              />
            </div>
            <Select
              id={`f-leg-${i}-right`}
              label="side"
              value={String(w["side"] ?? "put")}
              options={["put", "call"]}
              words={{ put: "Put", call: "Call" }}
              onChange={(v) => setLeg({ ...w, side: v })}
            />
            <Select id={`f-leg-${i}-rule`} label="strike by" value={rule} options={Object.keys(schema.strike_rules)} onChange={(r) => setLeg({ ...leg, strike: { rule: r } })} />
            <div className="builder-leg-rule">
              {/* A rule placed off the spot has no leg to name: keep its cell, so points and pct sit
                  in the same columns as a rule placed off another leg's. */}
              {def && !("leg" in def.keys) && ("points" in def.keys || "pct" in def.keys) && <div aria-hidden="true" />}
              <KeyFields
                prefix={`f-leg-${i}`}
                keys={def?.keys ?? {}}
                types={schema.types?.strike}
                legs={legs.slice(0, i).map((l) => String(l["name"] ?? ""))}
                legLabels={labels}
                o={strike}
                onChange={(next) => setLeg({ ...leg, strike: { ...next, rule } })}
              />
            </div>
            <div className="builder-leg-match">
              {def && def.matches.length > 0 && (
                <Select
                  id={`f-leg-${i}-match`}
                  label="match"
                  value={String(strike["match"] ?? "")}
                  options={def.matches}
                  blank={def.default_match ? `default (${def.default_match})` : "required: choose one"}
                  onChange={(m) => setLeg({ ...leg, strike: withKey(strike, "match", m === "" ? undefined : m) })}
                />
              )}
            </div>
            <button type="button" className="btn builder-remove" aria-label={`Remove leg ${labels[name] ?? name}`} onClick={() => set("legs", legs.filter((_, j) => j !== i))}>
              ×
            </button>
          </div>
        );
      })}
      </div>
      <div className="builder-leg-add">
        <button
          type="button"
          className="btn"
          onClick={() => {
            const id = nextLegId(legs, given.current);
            given.current = Number(id.slice(1));
            set("legs", [...legs, { name: id, action: "STO", qty: 1, side: "put", strike: { rule: "delta" } }]);
          }}
        >
          + Add leg ({nextLegId(legs, given.current)})
        </button>
        <span className="muted">Each leg keeps its ID: a removed one is not given again, and a new leg takes the next number.</span>
      </div>
    </>
  );
}

/** Section 5's exit keys, one field each. */
export function ExitFields({ schema, spec, onChange }: SectionProps) {
  const exit = obj(spec["exit"]);
  return (
    <div className="builder-grid">
      {schema.exits.map((k) => (
        <Field id={`f-exit-${k}`} label={k} value={exit[k]} t={schema.types?.exit[k]} onChange={(v) => onChange(withKey(spec, "exit", withKey(exit, k, v)))} />
      ))}
    </div>
  );
}

/** The limits in reading order: how many at once, how many a day, how often, then the rest the
 *  module offers. */
const LIMIT_ORDER = ["max_open", "per_day", "every_days", "one_per_expiry"];

export function orderedLimits(limits: readonly string[]): string[] {
  const known = LIMIT_ORDER.filter((k) => limits.includes(k));
  return [...known, ...limits.filter((k) => !LIMIT_ORDER.includes(k))];
}

/** Section 5's size and limits, each its own labelled group. */
export function SizeFields({ schema, spec, onChange }: SectionProps) {
  const set = (key: string, value: unknown) => onChange(withKey(spec, key, value));
  const size = obj(spec["size"]);
  const sizeKind = Object.keys(schema.sizes).find((k) => k in size) ?? "contracts";
  const limits = obj(spec["limits"]);
  return (
    <>
      <h5 className="builder-lbl builder-sub">size</h5>
      <div className="builder-grid">
        <Select id="f-size-kind" label="size by" value={sizeKind} options={Object.keys(schema.sizes)} onChange={(k) => set("size", { [k]: 1 })} />
        {(schema.sizes[sizeKind] ?? []).map((k) => (
          <Field id={`f-size-${k}`} label={k} value={size[k]} required={k === sizeKind} t={schema.types?.size[k]} onChange={(v) => set("size", withKey(size, k, v))} />
        ))}
      </div>
      <h5 className="builder-lbl builder-sub">limits</h5>
      <div className="builder-grid">
        {orderedLimits(schema.limits).map((k) => (
          <Field
            id={`f-limit-${k}`}
            label={k}
            value={limits[k]}
            required={k === "max_open" || k === "per_day"}
            t={schema.types?.limits[k]}
            onChange={(v) => set("limits", withKey(limits, k, v))}
          />
        ))}
      </div>
    </>
  );
}

/** The structure's name: the strategy filled in, "Custom (from Iron condor)" once its legs were
 *  edited, "Custom" when no strategy filled them. */
export function structureName(filled: { label: string; legs: string } | null, legs: unknown): string {
  if (filled === null) return "Custom";
  return JSON.stringify(legs) === filled.legs ? filled.label : `Custom (from ${filled.label})`;
}

export function BuilderForm({
  schema,
  spec,
  labels,
  onChange,
  readOnly = false,
  shape,
}: {
  schema: BuilderSchema;
  spec: Spec;
  labels: Record<string, BuilderBlock["state"]>;
  onChange: (next: Spec) => void;
  /** A saved automation: every field shown and none changeable (each section's fields sit in a
   *  disabled fieldset); the section help and the event key still open. */
  readOnly?: boolean;
  /** The legs' shape and chain example under the structure: the automation's name, and the edit as
   *  it stands when it is not the saved one. */
  shape?: { name: string; automation?: Spec };
}) {
  const p = { schema, spec, onChange };
  // What the strategy selector last filled in: its name and the legs as filled, so an edit reads Custom.
  const [filled, setFilled] = useState<{ label: string; legs: string } | null>(null);
  const lock = (body: React.ReactNode) => (
    <fieldset className="builder-lock" disabled={readOnly}>
      {body}
    </fieldset>
  );
  return (
    <section className="builder-form" aria-label="The automation, block by block">
      {!readOnly && (
        <p className="muted builder-form-key">
          <span className="builder-req">*</span> required. A greyed value in an empty field is the engine's default: left blank, the engine uses it.
        </p>
      )}
      <div className="builder-section">
        <SectionHead n={1} title="Underlying and data" blocks={["structure"]} labels={labels} help="underlying" />
        <UnderlyingFields {...p} readOnly={readOnly} />
      </div>

      <div className="builder-section">
        <SectionHead n={2} title="Signal: when to enter" blocks={["entry"]} labels={labels} help="signal" />
        {lock(<EntryFields {...p} />)}
      </div>

      <div className="builder-section">
        <SectionHead n={3} title="Gates: the market and the chain" blocks={["gates"]} labels={labels} note="read before the legs are chosen" help="gates" />
        {lock(<GateFields {...p} stages={["signal", "chain"]} prefix="f-gate" />)}
      </div>

      <div className="builder-section">
        <SectionHead n={4} title="Structure: legs, then price" blocks={["structure"]} labels={labels} help="structure" />
        {!readOnly && schema.strategy && (
          <StrategyPicker
            schema={schema.strategy}
            edited={list(spec["legs"]).length > 0 && JSON.stringify(spec["legs"]) !== filled?.legs}
            onFill={(r) => {
              onChange({ ...spec, legs: r.legs ?? [], expiry: r.expiry ?? spec["expiry"] });
              setFilled({ label: schema.strategy!.strategies[r.strategy]?.label ?? r.strategy, legs: JSON.stringify(r.legs ?? []) });
            }}
          />
        )}
        {!readOnly && (
        <p className="muted builder-structure">
          structure: <b>{structureName(filled, spec["legs"])}</b>
        </p>
        )}
        {lock(<LegFields {...p} />)}
        {shape && <TradeShape name={shape.name} automation={shape.automation} />}
        <h5 className="builder-lbl">price and structure gates · read once the legs are chosen</h5>
        {lock(<GateFields {...p} stages={["structure"]} prefix="f-sgate" />)}
      </div>

      <div className="builder-section">
        <SectionHead n={5} title="Exits, size, costs" blocks={["exit", "size"]} labels={labels} help="exits" />
        {lock(
          <>
            <h5 className="builder-lbl builder-sub">exits</h5>
            <ExitFields {...p} />
            <SizeFields {...p} />
          </>,
        )}
        <p className="muted">Fills are paper's (mid with the suite's slippage model) and fees are core's: neither is set here. P&amp;L is net; fees, settlement and slippage each get their own column on the run card.</p>
      </div>
    </section>
  );
}
