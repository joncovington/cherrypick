import { useRef, useState } from "react";
import type { BuilderFieldType, BuilderSchema } from "@console/shared";
import { TypedField, showValue, type FieldProps } from "./BuilderField";

export { parseValue, showValue } from "./BuilderField";
import { isLegId, legLabels, legWords, nextLegId, openVerb, signedQty, titleCase, writtenLeg } from "./legWords";

/**
 * The Builder's field editors: the legs (Edit legs), the far expiry and the event list, each field
 * writing the same spec the JSON editor holds and the paper loop reads. Every choice comes from the
 * module's schema (`cli builder-schema`, the engine's own tables); whether a value is right is the
 * module's Check. A blank field removes its key. The page's sentences (`BuilderForm`) lay these in
 * where a line cannot hold them.
 */

export type Spec = Record<string, unknown>;
type Obj = Record<string, unknown>;

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];

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

export function Select({
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
export function KeyFields({
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

/** What every section's fields take: the schema, the whole spec and how to change it. */
export interface SectionProps {
  schema: BuilderSchema;
  spec: Spec;
  onChange: (next: Spec) => void;
}

/** The far expiry (#1318): a calendar's or a diagonal's second expiry, its rule set from the near one
 *  (`after_near`) or from today, and the legs that sit on it pick it in their Expiry column. Absent,
 *  a button adds it; removed, every leg goes back to the near expiry. */
export function FarExpiry({ schema, spec, onChange }: SectionProps) {
  const kinds = schema.far_expiry ?? {};
  const rule = spec["far_expiry"] === undefined ? null : obj(spec["far_expiry"]);
  if (rule === null) {
    return (
      <div className="builder-far">
        <button type="button" className="btn" onClick={() => onChange(withKey(spec, "far_expiry", { kind: "after_near", days: 28 }))}>
          + Far expiry
        </button>
        <span className="muted">For a calendar or a diagonal: a second expiry, and each leg picks Near or Far.</span>
      </div>
    );
  }
  const kind = String(rule["kind"] ?? "after_near");
  const remove = () => {
    const legs = list(spec["legs"]).map((l) => withKey(obj(l), "expiry", undefined));
    onChange(withKey(withKey(spec, "far_expiry", undefined), "legs", legs));
  };
  return (
    <div className="builder-grid builder-far-row">
      <Select
        id="f-far-kind"
        label="far expiry"
        value={kind}
        options={Object.keys(kinds)}
        words={{ after_near: "after near" }}
        onChange={(k) => onChange(withKey(spec, "far_expiry", { kind: k }))}
      />
      <KeyFields
        prefix="f-far"
        keys={kinds[kind] ?? {}}
        flags={schema.expiry_flags}
        types={schema.types?.expiry}
        o={rule}
        onChange={(next) => onChange(withKey(spec, "far_expiry", { ...next, kind }))}
      />
      <button type="button" className="btn builder-remove" aria-label="Remove the far expiry" onClick={remove}>
        ×
      </button>
    </div>
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
  // A leg picks its expiry once the automation has a far one (or a leg already sits on it).
  const twoExpiries = spec["far_expiry"] !== undefined || legs.some((l) => l["expiry"] === "far");
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
          <div key={`leg-${i}`} className={`builder-cond builder-leg-row${twoExpiries ? " has-expiry" : ""}`}>
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
            {twoExpiries && (
              <Select
                id={`f-leg-${i}-expiry`}
                label="expiry"
                value={leg["expiry"] === "far" ? "far" : "near"}
                options={schema.leg_expiries ?? ["near", "far"]}
                words={{ near: "Near", far: "Far" }}
                onChange={(v) => setLeg(withKey(w, "expiry", v === "far" ? "far" : undefined))}
              />
            )}
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

/** The automation once a strategy fills it in: its legs and expiry, its far expiry (or none, so a
 *  one-expiry strategy leaves no far rule behind), and for a far leg the close before the near expiry
 *  the engine requires, kept where the automation already sets one. */
export function withStrategy(spec: Spec, r: { legs?: Array<Record<string, unknown>>; expiry?: Record<string, unknown>; far_expiry?: Record<string, unknown>; spec?: Record<string, unknown> }): Spec {
  let next = withKey({ ...spec, legs: r.legs ?? [], expiry: r.expiry ?? spec["expiry"] }, "far_expiry", r.far_expiry);
  const close = obj(obj(r.spec)["exit"])["close_before_expiry_min"];
  if (r.far_expiry && close !== undefined && obj(spec["exit"])["close_before_expiry_min"] === undefined) {
    next = { ...next, exit: { ...obj(spec["exit"]), close_before_expiry_min: close } };
  }
  return next;
}

/** The gates once an entry preset is filled again: the gates it added last time, where they still
 *  stand as added, give way to its filters' new ones; every other gate (and one edited since) stays,
 *  in its place. */
export function withFilterGates(gates: unknown, added: string[], next: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  const left = [...added];
  const kept = list(gates)
    .map(obj)
    .filter((g) => {
      const i = left.indexOf(JSON.stringify(g));
      if (i < 0) return true;
      left.splice(i, 1);
      return false;
    });
  return [...kept, ...next];
}
