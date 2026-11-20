import { useState, type ReactNode } from "react";
import type { BuilderFieldType, BuilderSchema, BuilderWords } from "@console/shared";
import { eventName, withKey, type Spec } from "./BuilderFields";
import { FormSelect, TriggerLines } from "./lines";
import { at, Line, setAt, Slot } from "./sentence";
import { list, obj } from "./specWords";

/**
 * The Builder's filters (section 3) and exits (section 4) as the module's sentences (`words.py`).
 *
 * **Filters:** one list for every gate, each a line with its stage's tag (read before the order, on
 * the chain, on the priced trade). A filter's min and max read as one band; either side left blank
 * reads "at least" or "at most". **+ Add a filter** opens the module's gates by group.
 *
 * **Exits:** a starting set (the module's credit-trade rules, or held to expiry), then a row for each
 * of the module's exit rows (profit, loss, time, events) with its exits as lines, more than one where
 * the trade has more (a credit stop and a dollar stop together), and the rest under "More options".
 * Whether the trade enters for a credit or a debit is the module's, read from the legs
 * (`core.structures.entry_kind`, the payoff's `entry_kind`): a stop the trade cannot fire is not
 * offered. **Edit as rule** shows the exits as the engine reads them, and opens by itself, naming
 * what it holds, when the exits set something the lines cannot show.
 */

type Obj = Record<string, unknown>;

/** A filter's min and max as one band: the module's "between {min} and {max}", either side blank. */
function RangeControl({ W, g, t, id, onChange }: { W: BuilderWords; g: Obj; t: Record<string, BuilderFieldType> | undefined; id: string; onChange: (g: Obj) => void }) {
  return (
    <Line
      template={W.range.both}
      control={(slot) => (
        <Slot id={`${id}-${slot}`} label={slot === "min" ? "at least" : "at most"} t={t?.[slot] ?? { type: "number" }} value={g[slot]} width={64} onChange={(v) => onChange(withKey(g, slot, v))} />
      )}
    />
  );
}

/** The gates of `names` by the module's group, in the order each group first appears. */
function byGroup(W: BuilderWords, names: string[]): Array<[string, string[]]> {
  const out = new Map<string, string[]>();
  for (const k of names) out.set(W.gates[k]?.group ?? "Other", [...(out.get(W.gates[k]?.group ?? "Other") ?? []), k]);
  return [...out.entries()];
}

export function FiltersSection({ schema, W, spec, onChange }: { schema: BuilderSchema; W: BuilderWords; spec: Spec; onChange: (s: Spec) => void }) {
  const [picking, setPicking] = useState(false);
  const gates = list(spec["gates"]).map(obj);
  const defs = Object.fromEntries(schema.gates.map((g) => [g.gate, g]));
  const setGates = (next: Obj[]) => onChange(withKey(spec, "gates", next.length ? next : undefined));
  const setGate = (i: number, g: Obj) => setGates(gates.map((x, j) => (j === i ? g : x)));
  return (
    <>
      <p className="b-sub">Every filter must pass. Leave one side of a band blank for "at least" or "at most".</p>
      <div className="b-filters">
        {gates.length === 0 && <p className="muted">No filters: every day the entry allows is entered.</p>}
        {gates.map((g, i) => {
          const name = String(g["gate"] ?? "");
          const w = W.gates[name];
          const t = schema.types?.gates[name];
          const stage = defs[name]?.stage;
          return (
            <div key={`${name}-${i}`} className="b-filter">
              <span className="b-filter-name" title={w?.about}>
                {w?.label ?? name}
              </span>
              {w ? (
                <Line
                  template={w.sentence}
                  control={(slot) =>
                    slot === "range" ? (
                      <RangeControl W={W} g={g} t={t} id={`g-${i}`} onChange={(next) => setGate(i, next)} />
                    ) : (
                      <Slot id={`g-${i}-${slot}`} label={slot.replaceAll("_", " ")} wordKey={slot} t={t?.[slot]} w={w} value={g[slot]} width={60} onChange={(v) => setGate(i, withKey(g, slot, v))} />
                    )
                  }
                />
              ) : (
                <code>{JSON.stringify(g)}</code>
              )}
              {Object.entries(w?.more ?? {}).map(([k, phrase]) => (
                <span key={k} className="b-filter-more">
                  <Line template={phrase} control={(slot) => <Slot id={`g-${i}-${slot}`} label={slot.replaceAll("_", " ")} t={t?.[slot]} w={w} value={g[slot]} width={52} onChange={(v) => setGate(i, withKey(g, slot, v))} />} />
                </span>
              ))}
              {stage && W.stages?.[stage] && <span className="chip b-stage">{W.stages[stage]}</span>}
              <button type="button" className="b-x" aria-label={`Remove filter ${w?.label ?? name}`} onClick={() => setGates(gates.filter((_, j) => j !== i))}>
                ×
              </button>
            </div>
          );
        })}
      </div>
      <div>
        <button type="button" className="btn" aria-expanded={picking} onClick={() => setPicking(!picking)}>
          + Add a filter
        </button>
      </div>
      {picking && (
        <div className="b-picker" role="group" aria-label="Filters to add">
          {byGroup(
            W,
            schema.gates.map((g) => g.gate),
          ).map(([group, names]) => (
            <div key={group} className="b-pgroup">
              <h3 className="b-h3">{group}</h3>
              {names.map((k) => (
                <button
                  key={k}
                  type="button"
                  className="b-pitem"
                  onClick={() => {
                    setGates([...gates, { gate: k }]);
                    setPicking(false);
                  }}
                >
                  <b>{W.gates[k]?.label ?? k}</b>
                  <span>{W.gates[k]?.about}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

// --------------------------------------------------------------------------- exits
/** Whether the trade can fire this exit: a credit stop needs a credit, a debit stop a debit. A trade
 *  whose rule does not say (or a page with no answer yet) takes either. */
export function exitFits(k: string, kind: "credit" | "debit" | null | undefined): boolean {
  if (k === "stop_credit_multiple") return kind !== "debit";
  if (k === "stop_debit_pct") return kind !== "credit";
  return true;
}

/** What the exits hold that their lines cannot show: a key the module words no line for, or a part of
 *  an exit that is not one of its parts. */
export function unshown(W: BuilderWords, exit: Obj): string[] {
  const out: string[] = [];
  for (const [k, v] of Object.entries(exit)) {
    if (!W.exits[k]) {
      out.push(k);
      continue;
    }
    const parts = W.exit_parts[k];
    if (parts && typeof v === "object" && v !== null && !Array.isArray(v)) {
      for (const p of Object.keys(v as Obj)) if (!(p in parts)) out.push(`${k}.${p}`);
    }
  }
  return out;
}

/** Which starting set the exits are: the credit-trade rules, held to expiry, or neither (Custom). */
export function exitStart(schema: BuilderSchema, exit: Obj): "credit_rules" | "hold" | null {
  if (Object.keys(exit).length === 0) return "hold";
  return JSON.stringify(Object.entries(exit).sort()) === JSON.stringify(Object.entries(schema.credit_rules).sort()) ? "credit_rules" : null;
}

/** A part of an exit's type: the minutes before the close are whole, the rest any number. */
function partType(slot: string): BuilderFieldType {
  return slot === "avoid_events.before_close_min" ? { type: "int", min: 0, max: 390, default: 60 } : { type: "number" };
}

/** One exit as its line, each slot a control: a value, a part of an object, the events, or a trigger. */
function ExitLine({ schema, W, k, exit, onChange }: { schema: BuilderSchema; W: BuilderWords; k: string; exit: Obj; onChange: (x: Obj) => void }) {
  const w = W.exits[k]!;
  const set = (slot: string, v: unknown) => onChange(setAt(exit, slot, v) as Obj);
  const control = (slot: string): ReactNode => {
    if (slot === k) return <Slot id={`x-${k}`} label={w.label ?? k} t={schema.types?.exit[k]} value={exit[k]} width={64} onChange={(v) => set(k, v)} />;
    if (slot === "avoid_events.labels") {
      const on = list(at(exit, slot)).map(String);
      return (
        <>
          {on.map((l) => (
            <span key={l} className="b-tag">
              {eventName(schema, l)}
              <button type="button" aria-label={`Stop closing ahead of ${eventName(schema, l)}`} onClick={() => set(slot, on.filter((x) => x !== l))}>
                ×
              </button>
            </span>
          ))}
          <select className="ctl b-add" aria-label="Add an event to close ahead of" value="" onChange={(e) => e.currentTarget.value && set(slot, [...on, e.currentTarget.value])}>
            <option value="">+ Add an event</option>
            {schema.event_labels
              .filter((l) => !on.includes(l))
              .map((l) => (
                <option key={l} value={l}>
                  {eventName(schema, l)}
                </option>
              ))}
          </select>
        </>
      );
    }
    if (slot === "signal.trigger") return <FormSelect W={W} schema={schema} trig={obj(at(exit, slot))} onChange={(t) => set(slot, t)} />;
    return <Slot id={`x-${slot.replace(".", "-")}`} label={slot.split(".").pop()!.replaceAll("_", " ")} t={partType(slot)} value={at(exit, slot)} width={60} onChange={(v) => set(slot, v)} />;
  };
  return (
    <>
      <span className="b-exit-sentence">
        <Line template={w.sentence} control={control} />
        {Object.entries(w.more ?? {}).map(([p, phrase]) => (
          <span key={p} className="b-filter-more">
            <Line template={phrase} control={control} />
          </span>
        ))}
      </span>
      {k === "signal" && (
        <div className="b-exit-trigger">
          <TriggerLines W={W} schema={schema} trig={obj(at(exit, "signal.trigger"))} onChange={(t) => set("signal.trigger", t)} id="x-sig" />
        </div>
      )}
    </>
  );
}

/** What a newly added exit starts as: an object for one with parts (filled in on its line), else a
 *  key with no value yet, kept on the page until it has one. */
function freshExit(W: BuilderWords, k: string): unknown {
  return W.exit_parts[k] ? (k === "signal" ? { trigger: { signal: {}, window: {}, within: null } } : {}) : undefined;
}

export function ExitsSection({ schema, W, spec, onChange, kind }: { schema: BuilderSchema; W: BuilderWords; spec: Spec; onChange: (s: Spec) => void; kind: "credit" | "debit" | null | undefined }) {
  const exit = obj(spec["exit"]);
  const [pending, setPending] = useState<string[]>([]);
  const [confirm, setConfirm] = useState<string | null>(null);
  const setExit = (next: Obj) => onChange(withKey(spec, "exit", Object.keys(next).length ? next : undefined));
  const shown = (k: string) => exit[k] !== undefined || pending.includes(k);
  const add = (k: string) => {
    const v = freshExit(W, k);
    if (v === undefined) setPending([...pending, k]);
    else setExit({ ...exit, [k]: v });
  };
  const remove = (k: string) => {
    setPending(pending.filter((x) => x !== k));
    setExit(withKey(exit, k, undefined));
  };
  const keysOf = (group: string) => Object.keys(W.exits).filter((k) => W.exits[k]!.group === group && schema.exits.includes(k));
  const rows = W.exit_rows ?? [];
  const otherGroups = [...new Set(Object.values(W.exits).map((w) => w.group ?? ""))].filter((g) => g && !rows.includes(g));
  const starts = W.exit_starts ?? {};
  const current = exitStart(schema, exit);
  const startValue = (s: string): Obj => (s === "credit_rules" ? { ...schema.credit_rules } : {});
  const begin = (s: string) => {
    if (current === null && Object.keys(exit).length > 0 && confirm !== s) return setConfirm(s);
    setPending([]);
    setConfirm(null);
    setExit(startValue(s));
  };
  const hidden = unshown(W, exit);

  const group = (g: string, asRow: boolean) => {
    const keys = keysOf(g);
    const on = keys.filter(shown);
    const avail = keys.filter((k) => !shown(k) && exitFits(k, kind));
    if (!asRow && on.length === 0 && avail.length === 0) return null;
    return (
      <div key={g} className={`b-exit${on.length ? "" : " is-off"}`}>
        <div className="b-exit-q">
          {asRow && (
            <button
              type="button"
              role="switch"
              className={`b-sw${on.length ? " is-on" : ""}`}
              aria-checked={on.length > 0}
              aria-label={`${g} exits`}
              disabled={on.length === 0 && avail.length === 0}
              onClick={() => (on.length ? on.forEach(remove) : avail[0] && add(avail[0]))}
            />
          )}
          {g}
        </div>
        <div className="b-exit-body">
          {on.map((k) => (
            <div key={k} className="b-exit-line">
              <ExitLine schema={schema} W={W} k={k} exit={exit} onChange={setExit} />
              <button type="button" className="b-x" aria-label={`Remove ${W.exits[k]?.label ?? k}`} onClick={() => remove(k)}>
                ×
              </button>
            </div>
          ))}
          {avail.length > 0 && (
            <select className="ctl b-add" aria-label={`Add a ${g.toLowerCase()} exit`} value="" onChange={(e) => e.currentTarget.value && add(e.currentTarget.value)}>
              <option value="">{on.length ? "+ Add another" : "+ Add"}</option>
              {avail.map((k) => (
                <option key={k} value={k} title={W.exits[k]?.about}>
                  {W.exits[k]?.label ?? k}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>
    );
  };

  return (
    <>
      <div className="b-head">
        {kind && <span className="chip">this trade enters for a {kind}</span>}
        <span className="word">It closes at the first exit reached; none set holds it to expiry.</span>
      </div>
      <div className="b-starts" role="group" aria-label="Start from">
        {Object.entries(starts)
          .filter(([s]) => s !== "credit_rules" || kind !== "debit")
          .map(([s, w]) => (
            <button key={s} type="button" className={`b-start${current === s ? " is-on" : ""}`} aria-pressed={current === s} title={w.about} onClick={() => begin(s)}>
              {w.label}
            </button>
          ))}
        <span className={`b-start${current === null ? " is-on" : ""}`} aria-current={current === null ? "true" : undefined}>
          Custom
        </span>
      </div>
      {confirm && starts[confirm] && (
        <div className="b-confirm" role="alert">
          <span>The exits below are your own. Replace them with {starts[confirm]!.label}?</span>
          <button type="button" className="btn btn-primary" onClick={() => begin(confirm)}>
            Replace them
          </button>
          <button type="button" className="btn" onClick={() => setConfirm(null)}>
            Keep my exits
          </button>
        </div>
      )}
      <div className="b-exits">{rows.map((g) => group(g, true))}</div>
      <details className="b-more">
        <summary>
          More options <span className="muted">{otherGroups.flatMap(keysOf).filter(shown).length || "none"} set</span>
        </summary>
        <div className="b-exits">{otherGroups.map((g) => group(g, false))}</div>
      </details>
      <details className="b-more" open={hidden.length > 0 || undefined}>
        <summary>
          Edit as rule <span className="muted">{hidden.length ? "opened by itself" : "the lines above show all of this rule"}</span>
        </summary>
        {hidden.length > 0 && (
          <div className="b-flag" role="note">
            <b>!</b>
            <span>
              This rule also sets <code>{hidden.join(", ")}</code>, which the lines above don't show. It's kept as written; change it here.
            </span>
          </div>
        )}
        <textarea
          key={JSON.stringify(exit)}
          className="builder-editor-text"
          aria-label="the exits as JSON"
          spellCheck={false}
          rows={Math.min(14, JSON.stringify(exit, null, 2).split("\n").length + 1)}
          defaultValue={JSON.stringify(exit, null, 2)}
          onBlur={(e) => {
            const el = e.currentTarget;
            try {
              const v = JSON.parse(el.value) as unknown;
              if (typeof v !== "object" || v === null || Array.isArray(v)) throw new Error("an object");
              el.setCustomValidity("");
              if (JSON.stringify(v) !== JSON.stringify(exit)) setExit(v as Obj);
            } catch {
              el.setCustomValidity("The exits are a JSON object");
              el.reportValidity();
            }
          }}
        />
      </details>
    </>
  );
}
