import type { BuilderBlock, BuilderSchema } from "@console/shared";
import { CardHelpButton } from "../../components/CardHelp";
import { SECTION_HELP } from "./BuilderHelp";

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
  if (v === undefined) return "";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

/** `o` with `key` set to `value`, or removed when the value is undefined. */
export function withKey(o: Obj, key: string, value: unknown): Obj {
  const out = { ...o };
  if (value === undefined) delete out[key];
  else out[key] = value;
  return out;
}

function Field({ id, label, value, onChange, required }: { id: string; label: string; value: unknown; onChange: (v: unknown) => void; required?: boolean }) {
  return (
    <div className="builder-f">
      <label className="builder-lbl" htmlFor={id}>
        {label.replaceAll("_", " ")}
        {required ? " *" : ""}
      </label>
      <input id={id} className="builder-input" defaultValue={showValue(value)} onBlur={(e) => onChange(parseValue(e.currentTarget.value))} />
    </div>
  );
}

function Select({
  id,
  label,
  value,
  options,
  onChange,
  blank,
}: {
  id: string;
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  /** What an unset value means (the engine's default); offered as the first choice. */
  blank?: string;
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
            {o.replaceAll("_", " ")}
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
      <input id={id} type="checkbox" checked={value === true} onChange={(e) => onChange(e.currentTarget.checked ? true : undefined)} />
    </div>
  );
}

/** The keys a kind takes ({key: required}), each a field on `o`; a key in `flags` is a checkbox. */
function KeyFields({
  prefix,
  keys,
  o,
  onChange,
  skip = [],
  flags = [],
}: {
  prefix: string;
  keys: Record<string, boolean>;
  o: Obj;
  onChange: (next: Obj) => void;
  skip?: string[];
  flags?: string[];
}) {
  return (
    <>
      {Object.entries(keys)
        .filter(([k]) => !skip.includes(k))
        .map(([k, req]) =>
          flags.includes(k) ? (
            <Flag key={`${prefix}-${k}`} id={`${prefix}-${k}`} label={k} value={o[k]} onChange={(v) => onChange(withKey(o, k, v))} />
          ) : (
            <Field key={`${prefix}-${k}-${showValue(o[k])}`} id={`${prefix}-${k}`} label={k} value={o[k]} required={req} onChange={(v) => onChange(withKey(o, k, v))} />
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
      {shown.map(({ g, i }) => {
        const def = byName[String(g["gate"])];
        return (
          <div key={`${prefix}-${i}-${String(g["gate"])}`} className="builder-cond">
            <Select
              id={`${prefix}-${i}`}
              label="gate"
              value={String(g["gate"] ?? "")}
              options={known.map((k) => k.gate)}
              onChange={(name) => onChange(gates.map((x, j) => (j === i ? { gate: name } : x)))}
            />
            {(def?.params ?? []).map((p) => (
              <Field
                key={`${prefix}-${i}-${p.name}-${showValue(g[p.name])}`}
                id={`${prefix}-${i}-${p.name}`}
                label={p.default === null || p.default === undefined ? p.name : `${p.name} (${showValue(p.default)})`}
                value={g[p.name]}
                required={p.required}
                onChange={(v) => onChange(gates.map((x, j) => (j === i ? withKey(x, p.name, v) : x)))}
              />
            ))}
            <button type="button" className="btn builder-remove" aria-label={`Remove gate ${String(g["gate"])}`} onClick={() => onChange(gates.filter((_, j) => j !== i))}>
              ×
            </button>
          </div>
        );
      })}
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

/** Section 1's fields: symbol, expiry rule, weekdays, event skips. */
export function UnderlyingFields({ schema, spec, onChange }: SectionProps) {
  const set = (key: string, value: unknown) => onChange(withKey(spec, key, value));
  const expiry = obj(spec["expiry"]);
  const skip = obj(spec["skip_events"]);
  const labelsOn = list(skip["labels"]).map(String);
  const weekdays = list(spec["weekdays"]).map(Number);
  const exprKind = String(expiry["kind"] ?? "dte");
  return (
    <>
      <div className="builder-grid">
        <Field key={`sym-${showValue(spec["symbol"])}`} id="f-symbol" label="symbol" value={spec["symbol"]} required onChange={(v) => set("symbol", v)} />
        <Select id="f-expiry-kind" label="expiry" value={exprKind} options={Object.keys(schema.expiry)} onChange={(k) => set("expiry", { kind: k })} />
        <KeyFields prefix="f-expiry" keys={schema.expiry[exprKind] ?? {}} flags={schema.expiry_flags} o={expiry} onChange={(next) => set("expiry", { ...next, kind: exprKind })} />
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
      <fieldset className="builder-days">
        <legend className="builder-lbl">skip on these events</legend>
        {schema.event_labels.map((l) => (
          <label key={l}>
            <input
              type="checkbox"
              checked={labelsOn.includes(l)}
              onChange={(e) => {
                const next = e.currentTarget.checked ? [...labelsOn, l] : labelsOn.filter((x) => x !== l);
                set("skip_events", next.length ? { ...skip, labels: next } : undefined);
              }}
            />{" "}
            {l}
          </label>
        ))}
      </fieldset>
    </>
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
  return (
    <>
      <div className="builder-grid">
        <Select id="f-entry-kind" label="entry" value={entryKind} options={Object.keys(schema.entry)} onChange={(k) => set("entry", { kind: k })} />
        {entryKind === "orb" ? (
          <>
            <KeyFields prefix="f-orb" keys={schema.entry["orb"] ?? {}} o={entry} skip={["direction"]} onChange={(next) => set("entry", { ...next, kind: "orb" })} />
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
              <KeyFields prefix="f-at" keys={schema.trigger_forms["at"] ?? {}} o={trigger} onChange={(next) => set("entry", { ...entry, trigger: next })} />
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
                    key={`f-sig-${p}-${showValue(signal[p])}`}
                    id={`f-sig-${p}`}
                    label={p}
                    value={signal[p]}
                    onChange={(v) => set("entry", { ...entry, trigger: { ...trigger, signal: withKey(signal, p, v) } })}
                  />
                ))}
                {(["open", "close"] as const).map((w) => (
                  <Field
                    key={`f-win-${w}-${showValue(obj(trigger["window"])[w])}`}
                    id={`f-win-${w}`}
                    label={`window ${w}`}
                    value={obj(trigger["window"])[w]}
                    required
                    onChange={(v) => set("entry", { ...entry, trigger: { ...trigger, window: withKey(obj(trigger["window"]), w, v) } })}
                  />
                ))}
                <Field
                  key={`f-within-${showValue(trigger["within"])}`}
                  id="f-within"
                  label="within (blank: until it closes)"
                  value={trigger["within"] ?? undefined}
                  onChange={(v) => set("entry", { ...entry, trigger: { ...trigger, within: v === undefined ? null : v } })}
                />
                <Field key={`f-daily-${showValue(trigger["daily"])}`} id="f-daily" label="daily" value={trigger["daily"]} onChange={(v) => set("entry", { ...entry, trigger: withKey(trigger, "daily", v) })} />
              </>
            )}
            <KeyFields prefix="f-trig" keys={schema.entry["trigger"] ?? {}} o={entry} skip={["trigger"]} onChange={(next) => set("entry", { ...next, kind: "trigger", trigger })} />
          </>
        ) : (
          <KeyFields prefix="f-time" keys={schema.entry["time"] ?? {}} o={entry} onChange={(next) => set("entry", { ...next, kind: "time" })} />
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

/** Section 4's legs: side, right, strike rule, its keys and match mode. */
export function LegFields({ schema, spec, onChange }: SectionProps) {
  const set = (key: string, value: unknown) => onChange(withKey(spec, key, value));
  const legs = list(spec["legs"]).map(obj);
  return (
    <>
      {legs.map((leg, i) => {
        const strike = obj(leg["strike"]);
        const rule = String(strike["rule"] ?? "");
        const def = schema.strike_rules[rule];
        const setLeg = (next: Obj) => set("legs", legs.map((x, j) => (j === i ? next : x)));
        return (
          <div key={`leg-${i}`} className="builder-cond">
            <Field key={`leg-${i}-name-${showValue(leg["name"])}`} id={`f-leg-${i}-name`} label="leg" value={leg["name"]} required onChange={(v) => setLeg(withKey(leg, "name", v))} />
            <Select id={`f-leg-${i}-side`} label="side" value={String(leg["side"] ?? "")} options={["-1", "1"]} onChange={(v) => setLeg({ ...leg, side: Number(v) })} />
            <Select id={`f-leg-${i}-right`} label="right" value={String(leg["right"] ?? "")} options={["P", "C"]} onChange={(v) => setLeg({ ...leg, right: v })} />
            <Select id={`f-leg-${i}-rule`} label="strike by" value={rule} options={Object.keys(schema.strike_rules)} onChange={(r) => setLeg({ ...leg, strike: { rule: r } })} />
            <KeyFields prefix={`f-leg-${i}`} keys={def?.keys ?? {}} o={strike} onChange={(next) => setLeg({ ...leg, strike: { ...next, rule } })} />
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
            <button type="button" className="btn builder-remove" aria-label={`Remove leg ${String(leg["name"])}`} onClick={() => set("legs", legs.filter((_, j) => j !== i))}>
              ×
            </button>
          </div>
        );
      })}
      <div>
        <button type="button" className="btn" onClick={() => set("legs", [...legs, { name: `leg_${legs.length + 1}`, right: "P", side: -1, strike: { rule: "delta" } }])}>
          + leg
        </button>
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
        <Field key={`f-exit-${k}-${showValue(exit[k])}`} id={`f-exit-${k}`} label={k} value={exit[k]} onChange={(v) => onChange(withKey(spec, "exit", withKey(exit, k, v)))} />
      ))}
    </div>
  );
}

/** Section 5's size and limits. */
export function SizeFields({ schema, spec, onChange }: SectionProps) {
  const set = (key: string, value: unknown) => onChange(withKey(spec, key, value));
  const size = obj(spec["size"]);
  const sizeKind = Object.keys(schema.sizes).find((k) => k in size) ?? "contracts";
  const limits = obj(spec["limits"]);
  return (
    <div className="builder-grid">
      <Select id="f-size-kind" label="size by" value={sizeKind} options={Object.keys(schema.sizes)} onChange={(k) => set("size", { [k]: 1 })} />
      {(schema.sizes[sizeKind] ?? []).map((k) => (
        <Field key={`f-size-${k}-${showValue(size[k])}`} id={`f-size-${k}`} label={k} value={size[k]} required={k === sizeKind} onChange={(v) => set("size", withKey(size, k, v))} />
      ))}
      {schema.limits.map((k) => (
        <Field
          key={`f-limit-${k}-${showValue(limits[k])}`}
          id={`f-limit-${k}`}
          label={k}
          value={limits[k]}
          required={k === "max_open" || k === "per_day"}
          onChange={(v) => set("limits", withKey(limits, k, v))}
        />
      ))}
    </div>
  );
}

export function BuilderForm({
  schema,
  spec,
  labels,
  onChange,
}: {
  schema: BuilderSchema;
  spec: Spec;
  labels: Record<string, BuilderBlock["state"]>;
  onChange: (next: Spec) => void;
}) {
  const p = { schema, spec, onChange };
  return (
    <section className="builder-form" aria-label="The automation, block by block">
      <div className="builder-section">
        <SectionHead n={1} title="Underlying and data" blocks={["structure"]} labels={labels} help="underlying" />
        <UnderlyingFields {...p} />
      </div>

      <div className="builder-section">
        <SectionHead n={2} title="Signal: when to enter" blocks={["entry"]} labels={labels} help="signal" />
        <EntryFields {...p} />
      </div>

      <div className="builder-section">
        <SectionHead n={3} title="Gates: the market and the chain" blocks={["gates"]} labels={labels} note="read before the legs are chosen" help="gates" />
        <GateFields {...p} stages={["signal", "chain"]} prefix="f-gate" />
      </div>

      <div className="builder-section">
        <SectionHead n={4} title="Structure: legs, then price" blocks={["structure"]} labels={labels} help="structure" />
        <LegFields {...p} />
        <h5 className="builder-lbl">price and structure gates · read once the legs are chosen</h5>
        <GateFields {...p} stages={["structure"]} prefix="f-sgate" />
      </div>

      <div className="builder-section">
        <SectionHead n={5} title="Exits, size, costs" blocks={["exit", "size"]} labels={labels} help="exits" />
        <ExitFields {...p} />
        <SizeFields {...p} />
        <p className="muted">Fills are paper's (mid with the suite's slippage model) and fees are core's: neither is set here. P&amp;L is net; fees, settlement and slippage each get their own column on the run card.</p>
      </div>
    </section>
  );
}
