import { useState, type ReactNode } from "react";
import type { BuilderChoiceWords, BuilderFieldType, BuilderSchema, BuilderWords } from "@console/shared";
import { eventName, withKey } from "./BuilderFields";
import { Line, Slot, without } from "./sentence";
import { list, obj } from "./specWords";

/**
 * The Builder's shared line pieces: a line with its lead, the "More options" list, a signal with its
 * indicators, a trigger's form and its lines. The entry and a signal exit draw the same ones.
 */

type Obj = Record<string, unknown>;

export function Row({ lead, children }: { lead: string; children: ReactNode }) {
  return (
    <div className="b-line">
      <span className="b-lead">{lead}</span>
      {children}
    </div>
  );
}

/** One "More options" line: its phrase with its control. */
export interface MoreItem {
  key: string;
  phrase: string;
  t: BuilderFieldType | undefined;
  w: BuilderChoiceWords | undefined;
  value: unknown;
  set: (v: unknown) => void;
}

export function More({ title, items, extra, id }: { title: string; items: MoreItem[]; extra?: ReactNode; id: string }) {
  const set = items.filter((i) => i.value !== undefined).length;
  return (
    <details className="b-more">
      <summary>
        {title} <span className="muted">{set ? `${String(set)} set` : items.length ? "all at their defaults" : ""}</span>
      </summary>
      <div className="b-opts">
        {items.map((it) => (
          <div key={it.key} className="b-opt">
            <Line
              template={it.phrase}
              control={(slot) => <Slot id={`${id}-${it.key}`} label={slot.replaceAll("_", " ")} wordKey={slot} t={it.t} w={it.w} value={it.value} onChange={it.set} width={60} />}
            />
          </div>
        ))}
        {extra}
      </div>
    </details>
  );
}

/** The signal kinds by the module's group, in the order each group first appears. */
export function signalGroups(W: BuilderWords, kinds: string[]): Array<[string, string[]]> {
  const out = new Map<string, string[]>();
  for (const k of kinds) {
    const g = W.signals[k]?.group ?? "Other";
    out.set(g, [...(out.get(g) ?? []), k]);
  }
  return [...out.entries()];
}

/** An indicator (or a number) as a pill: its choice, then its own line with a box for each setting. */
export function SourceControl({ W, schema, value, onChange, id }: { W: BuilderWords; schema: BuilderSchema; value: unknown; onChange: (v: unknown) => void; id: string }) {
  const [number, setNumber] = useState(typeof value === "number");
  const src = obj(value);
  const name = number ? "#" : String(src["name"] ?? "");
  const w = W.sources[name];
  const groups = new Map<string, string[]>();
  for (const k of Object.keys(schema.sources ?? {})) groups.set(W.sources[k]?.group ?? "Other", [...(groups.get(W.sources[k]?.group ?? "Other") ?? []), k]);
  const typeOf = (k: string): BuilderFieldType =>
    w?.values?.[k] ? { type: "enum", options: Object.keys(w.values[k]!) } : k === "anchor" ? { type: "text" } : { type: "int", min: 1 };
  return (
    <span className="b-pill">
      <select
        className="ctl"
        aria-label="indicator"
        value={name}
        onChange={(e) => {
          const n = e.currentTarget.value;
          if (n === "#") {
            setNumber(true);
            onChange(undefined);
          } else {
            setNumber(false);
            onChange(n ? { name: n, ...(W.sources[n]?.start ?? {}) } : undefined);
          }
        }}
      >
        {name === "" && <option value="">choose…</option>}
        <option value="#">a number</option>
        {[...groups.entries()].map(([g, ks]) => (
          <optgroup key={g} label={g}>
            {ks.map((k) => (
              <option key={k} value={k}>
                {W.sources[k]?.label ?? k}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {number ? (
        <Slot id={`${id}-n`} label="number" t={{ type: "number" }} value={typeof value === "number" ? value : undefined} onChange={onChange} width={64} />
      ) : (
        w &&
        (schema.sources?.[name] ?? []).length > 0 && (
          <Line
            template={w.sentence}
            control={(slot) => (
              <Slot id={`${id}-${slot}`} label={slot.replaceAll("_", " ")} t={typeOf(slot)} w={w} value={src[slot]} width={52} onChange={(v) => onChange(withKey(src, slot, v))} />
            )}
          />
        )
      )}
    </span>
  );
}

/** A signal: its kind, grouped as the module groups them, then its line with a control in each slot. */
export function SignalControls({ W, schema, sig, onChange, id }: { W: BuilderWords; schema: BuilderSchema; sig: Obj; onChange: (s: Obj) => void; id: string }) {
  const kind = String(sig["kind"] ?? "");
  const w = W.signals[kind];
  const types = schema.types?.signals.kinds[kind];
  const rules = Object.fromEntries(Object.entries(sig).filter(([k]) => schema.signal_rules.includes(k)));
  const events = Object.fromEntries(schema.event_labels.map((l) => [l, eventName(schema, l)]));
  const ww = w ? { ...w, values: { ...(w.values ?? {}), event: events } } : undefined;
  return (
    <>
      <select className="ctl" aria-label="signal" value={kind} onChange={(e) => onChange({ kind: e.currentTarget.value, ...(W.signals[e.currentTarget.value]?.start ?? {}), ...rules })}>
        {kind === "" && <option value="">choose a signal…</option>}
        {signalGroups(W, Object.keys(schema.signals)).map(([g, ks]) => (
          <optgroup key={g} label={g}>
            {ks.map((k) => (
              <option key={k} value={k}>
                {W.signals[k]?.label ?? k}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {w && (
        <Line
          template={w.sentence}
          control={(slot) =>
            slot === "a" || slot === "b" ? (
              <SourceControl W={W} schema={schema} value={sig[slot]} id={`${id}-${slot}`} onChange={(v) => onChange(withKey(sig, slot, v))} />
            ) : (
              <Slot id={`${id}-${slot}`} label={slot.replaceAll("_", " ")} t={types?.[slot]} w={ww} value={sig[slot]} width={56} onChange={(v) => onChange(withKey(sig, slot, v))} />
            )
          }
        />
      )}
    </>
  );
}

/** A trigger's `within`: any time before the window closes (null), or a number of minutes. */
export function WithinControl({ w, value, onChange }: { w: BuilderChoiceWords; value: unknown; onChange: (v: unknown) => void }) {
  const n = typeof value === "number";
  return (
    <>
      <select className="ctl" aria-label="when it enters" value={n ? "n" : "null"} onChange={(e) => onChange(e.currentTarget.value === "n" ? 5 : null)}>
        <option value="null">{w.values?.["within"]?.["null"] ?? "until the window closes"}</option>
        <option value="n">{(w.values?.["within"]?.["n"] ?? "within {within} minutes").replace("{within}", "N")}</option>
      </select>
      {n && (
        <Line
          template={w.values?.["within"]?.["n"] ?? "{within}"}
          control={() => <Slot id="e-within" label="within" t={{ type: "int", min: 1 }} value={value} width={52} onChange={(v) => onChange(v === undefined ? null : v)} />}
        />
      )}
    </>
  );
}

/** A trigger's form: at a fixed time, or when a signal fires. A change starts the form afresh. */
export function FormSelect({ W, schema, trig, onChange }: { W: BuilderWords; schema: BuilderSchema; trig: Obj; onChange: (t: Obj) => void }) {
  const form = "at" in trig ? "at" : "signal";
  return (
    <select className="ctl" aria-label="fires" value={form} onChange={(e) => onChange(e.currentTarget.value === "at" ? { at: "", within: 5 } : { signal: {}, window: {}, within: null })}>
      {Object.keys(schema.trigger_forms).map((f) => (
        <option key={f} value={f}>
          {W.trigger[f]?.label ?? f}
        </option>
      ))}
    </select>
  );
}

/** A trigger's lines: the signal, its window and its conditions, or the fixed time. An entry's and a
 *  signal exit's alike. */
export function TriggerLines({ W, schema, trig, onChange, id }: { W: BuilderWords; schema: BuilderSchema; trig: Obj; onChange: (t: Obj) => void; id: string }) {
  const form = "at" in trig ? "at" : "signal";
  const tw = W.trigger[form];
  const sig = obj(trig["signal"]);
  const setTrig = onChange;
  const setSig = (next: Obj) => setTrig({ ...trig, signal: next });
  const only = list(sig["only_when"]).map(obj);
  return (
    <>
        {form === "signal" && tw && (
          <>
            <Row lead="When">
              <SignalControls W={W} schema={schema} sig={sig} id={`${id}-sig`} onChange={setSig} />
            </Row>
            <Row lead="Between">
              <Line
                template={without(tw.sentence, "when {signal},", "between")}
                control={(slot) =>
                  slot === "within" ? (
                    <WithinControl w={tw} value={trig["within"]} onChange={(v) => setTrig({ ...trig, within: v })} />
                  ) : (
                    <Slot
                      id={`${id}-${slot}`}
                      label={slot.replace("window.", "window ")}
                      t={schema.types?.trigger[slot.replace("window.", "")]}
                      value={obj(trig["window"])[slot.replace("window.", "")]}
                      onChange={(v) => setTrig({ ...trig, window: withKey(obj(trig["window"]), slot.replace("window.", ""), v) })}
                    />
                  )
                }
              />
            </Row>
            {only.map((o, i) => (
              <Row key={i} lead="Only if">
                <SignalControls
                  W={W}
                  schema={schema}
                  sig={o}
                  id={`${id}-only-${i}`}
                  onChange={(next) => setSig(withKey(sig, "only_when", only.map((x, j) => (j === i ? next : x))))}
                />
                <button
                  type="button"
                  className="b-x"
                  aria-label="Remove condition"
                  onClick={() => setSig(withKey(sig, "only_when", only.length > 1 ? only.filter((_, j) => j !== i) : undefined))}
                >
                  ×
                </button>
              </Row>
            ))}
            <div>
              <button type="button" className="btn btn-ghost" onClick={() => setSig(withKey(sig, "only_when", [...only, { kind: "" }]))}>
                + Add a condition
              </button>
            </div>
          </>
        )}
        {form === "at" && tw && (
          <Row lead="At">
            <Line
              template={without(tw.sentence, "at")}
              control={(slot) => <Slot id={`${id}-at-${slot}`} label={slot} t={schema.types?.trigger[slot]} value={trig[slot]} width={56} onChange={(v) => setTrig(withKey(trig, slot, v))} />}
            />
          </Row>
        )}
    </>
  );
}
