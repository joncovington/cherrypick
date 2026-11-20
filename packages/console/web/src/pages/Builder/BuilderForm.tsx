import { useRef, useState, type ReactNode } from "react";
import type { BuilderBlock, BuilderChoiceWords, BuilderFieldType, BuilderPayoff, BuilderSchema, BuilderWords } from "@console/shared";
import { builderEntry, builderStrategy } from "../../lib/api";
import { ExitFields, FarExpiry, GateFields, LegFields, eventName, withFilterGates, withKey, withStrategy, type Spec } from "./BuilderFields";
import { Line, Marked, Slot, without, type Seg } from "./sentence";
import { automationWords, joined, list, lower, obj, strategySegs } from "./specWords";
import { grouped, presetStart, strategyStart } from "./presets";
import { TradeShapeView } from "./TradeShape";
import { Tip, TipText, holdsCloseSnapshot, useTips } from "./builderTips";
import { isLegId, legPlain, writtenLeg } from "./legWords";
import { CardHelpButton } from "../../components/CardHelp";
import { SECTION_HELP } from "./BuilderHelp";

export { parseValue, showValue, withKey } from "./BuilderFields";
export type { Spec } from "./BuilderFields";

/**
 * The Builder as one page (2026-11-20, replacing the full form and the wizard): five sections in order
 * (the trade, the entry, the filters, the exits, the size and limits), each a disclosure that closes
 * to its sentence. Every line is the module's own (`words.py`, the schema's `words`) with a control in
 * each slot, so the page words no rule of its own; a line writes the same spec the JSON editor holds
 * and the paper loop reads. The strategy and the entry preset fill in the trade and the entry through
 * the module (`cli builder-strategy`, `cli builder-entry`) and every value stays editable. Filters and
 * exits still draw one field per key; their lines come in the next step.
 */

type Obj = Record<string, unknown>;

export const SECTIONS = [
  { id: "trade", n: 1, name: "Trade", blocks: ["structure"], help: "underlying" },
  { id: "entry", n: 2, name: "Entry", blocks: ["entry"], help: "signal" },
  { id: "filters", n: 3, name: "Filters", blocks: ["gates"], help: "gates" },
  { id: "exits", n: 4, name: "Exits", blocks: ["exit"], help: "exits" },
  { id: "size", n: 5, name: "Size and limits", blocks: ["size"], help: "exits" },
] as const;
export type SectionId = (typeof SECTIONS)[number]["id"];
export type OpenSections = Record<SectionId, boolean>;

/** The strategy that filled the legs: its settings, and the legs as filled, so an edit reads Custom. */
export interface Strategy {
  name: string;
  settings: Record<string, string | number>;
  legs: string;
}

/** The entry preset that filled the entry: the entry as filled and the gates it added. */
export interface Preset {
  name: string;
  entry: string;
  gates: string[];
}

/** The module's payoff for the shape and the chain example (`usePayoff`). */
export interface Payoff {
  empty: boolean;
  data: BuilderPayoff | null;
  stale: boolean;
  last: BuilderPayoff | null;
}

const STATE_WORDS: Record<BuilderBlock["state"], string> = {
  backtested: "backtested",
  changed: "changed since its backtest",
  not_backtested: "not backtested",
};
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];

function Row({ lead, children }: { lead: string; children: ReactNode }) {
  return (
    <div className="b-line">
      <span className="b-lead">{lead}</span>
      {children}
    </div>
  );
}

/** One "More options" line: its phrase with its control. */
interface MoreItem {
  key: string;
  phrase: string;
  t: BuilderFieldType | undefined;
  w: BuilderChoiceWords | undefined;
  value: unknown;
  set: (v: unknown) => void;
}

function More({ title, items, extra, id }: { title: string; items: MoreItem[]; extra?: ReactNode; id: string }) {
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

/** A section: its number, name, its line while closed, and its state. */
function Sec({
  id,
  n,
  name,
  line,
  state,
  open,
  onToggle,
  readOnly,
  next,
  onNext,
  help,
  children,
}: {
  id: SectionId;
  n: number;
  name: string;
  help: string;
  line: Seg[];
  state: string;
  open: boolean;
  onToggle: (open: boolean) => void;
  readOnly: boolean;
  next?: string;
  onNext?: () => void;
  children: ReactNode;
}) {
  return (
    <details
      className="b-sec"
      id={`b-sec-${id}`}
      open={open}
      onToggle={(e) => {
        if (e.currentTarget.open !== open) onToggle(e.currentTarget.open);
      }}
    >
      <summary>
        <span className="b-sec-n">{n}</span>
        <span className="b-sec-name">{name}</span>
        <span className="b-sec-line">
          <Marked segs={line} />
        </span>
        <span className={`b-sec-state${state === "editing" ? " is-cur" : state === "backtested" ? " is-ok" : ""}`}>{state}</span>
        {SECTION_HELP[help] && (
          // The ? opens its help without opening or closing the section.
          <span className="b-sec-help" onClick={(e) => e.preventDefault()}>
            <CardHelpButton cardTitle={`${String(n)} · ${name}`} help={SECTION_HELP[help]} />
          </span>
        )}
      </summary>
      <div className="b-sec-body">
        <fieldset className="builder-lock" disabled={readOnly}>
          {children}
        </fieldset>
        {next && onNext && !readOnly && (
          <div className="b-next">
            <button type="button" className="btn" onClick={onNext}>
              Next: {next}
            </button>
          </div>
        )}
      </div>
    </details>
  );
}

/** A strategy setting's type, from the strategy schema. */
function settingType(schema: BuilderSchema, k: string): BuilderFieldType | undefined {
  const s = schema.strategy?.settings[k];
  if (!s) return undefined;
  return { type: s.type, options: s.options, min: s.min, max: s.max, above: s.above } as BuilderFieldType;
}

/** The legs as the strategy left them, in aligned columns: what each leg does and how its strike is placed. */
export function LegTable({ schema, W, legs, far }: { schema: BuilderSchema; W: BuilderWords; legs: Obj[]; far: boolean }) {
  if (legs.length === 0) return <p className="muted">No legs yet: pick a strategy, or Edit legs to add them.</p>;
  const byName = Object.fromEntries(legs.map((l) => [String(l["name"] ?? ""), l]));
  const ref = (n: unknown) => (Array.isArray(n) ? n : [n]).map((x) => (byName[String(x)] ? legPlain(byName[String(x)]!) : String(x))).join(" and ");
  return (
    <div className="b-legtable-wrap">
      <table className="b-legtable">
        <thead>
          <tr>
            <th>Leg</th>
            <th>Action</th>
            <th>Qty</th>
            <th>Side</th>
            {far && <th>Expiry</th>}
            <th>Strike</th>
            <th>Placed by</th>
            <th>From</th>
            <th>Match</th>
          </tr>
        </thead>
        <tbody>
          {legs.map((leg, i) => {
            const w = writtenLeg(leg);
            const strike = obj(leg["strike"]);
            const rule = String(strike["rule"] ?? "");
            const def = schema.strike_rules[rule];
            const key = ["target", "points", "pct", "times", "sd", "minutes", "strikes"].find((k) => strike[k] !== undefined);
            const name = String(leg["name"] ?? "");
            const from = strike["leg"] ?? strike["from"];
            return (
              <tr key={`${name}-${i}`}>
                <td>{isLegId(name) ? <span className="b-leg-id">{name}</span> : legPlain(leg)}</td>
                <td>{W.strategy_values["direction"]?.[w["action"] === "STO" ? "short" : "long"] ?? String(w["action"])}</td>
                <td className="num">{String(w["qty"] ?? 1)}</td>
                <td>{String(w["side"] ?? "")}</td>
                {far && <td>{leg["expiry"] === "far" ? "far" : "near"}</td>}
                <td className="num">{key ? `${String(strike[key])}${key === "pct" ? "%" : ""}` : "—"}</td>
                <td>{W.strikes[rule]?.label ?? rule}</td>
                <td className={from === undefined ? "dim" : undefined}>{from === undefined ? "—" : ref(from)}</td>
                <td className={strike["match"] === undefined ? "dim" : undefined}>{String(strike["match"] ?? def?.default_match ?? "—")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** What each event is, when it falls and its next day: the module's own words. */
function EventKey({ schema }: { schema: BuilderSchema }) {
  const about = schema.event_about;
  if (!about) return null;
  const labels = [...schema.event_labels].sort((a, b) => eventName(schema, a).localeCompare(eventName(schema, b)));
  return (
    <details className="b-more b-evkey">
      <summary>What each event is</summary>
      <table className="builder-event-key">
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
              <td className="num">{about[l]?.next ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

// --------------------------------------------------------------------------- 1 trade
function TradeSection({
  schema,
  W,
  spec,
  onChange,
  strategy,
  setStrategy,
  payoff,
  tips,
}: {
  schema: BuilderSchema;
  W: BuilderWords;
  spec: Spec;
  onChange: (s: Spec) => void;
  strategy: Strategy | null;
  setStrategy: (s: Strategy | null) => void;
  payoff: Payoff;
  tips: ReturnType<typeof useTips>;
}) {
  const specRef = useRef(spec);
  specRef.current = spec;
  const legs = list(spec["legs"]).map(obj);
  const [editing, setEditing] = useState(legs.length === 0);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const ss = schema.strategy;
  const edited = strategy !== null && JSON.stringify(spec["legs"] ?? []) !== strategy.legs;
  const name = strategy?.name ?? "custom";
  const def = ss?.strategies[name];
  const sw = W.strategies[name];

  const apply = async (k: string, settings: Record<string, string | number>, picked: boolean) => {
    const r = await builderStrategy(k, settings);
    if (r.error) return setProblem(r.error);
    if (r.problems?.length) return setProblem(r.problems.join("; "));
    setProblem(null);
    const cur = specRef.current;
    const next = withStrategy(cur, r);
    // A pick sets the expiry; a change to the strategy line rebuilds the legs and keeps the expiry line's.
    onChange(picked ? next : { ...next, expiry: cur["expiry"] });
    setStrategy({ name: k, settings, legs: JSON.stringify(r.legs ?? []) });
    setEditing(false);
    setConfirm(null);
  };
  const pick = (k: string) => {
    if (k === "custom") {
      setStrategy(null);
      setEditing(true);
      setConfirm(null);
      return;
    }
    if (!ss) return;
    if (legs.length > 0 && (strategy === null || edited) && confirm !== k) return setConfirm(k);
    void apply(k, strategyStart(ss, k), true);
  };
  const setSetting = (k: string, v: unknown) => {
    if (!strategy || !ss || v === undefined) return;
    const next = { ...strategy.settings, [k]: v as string | number };
    if (k === "place_by") next["target"] = ss.target_defaults[String(v)] ?? next["target"]!;
    void apply(strategy.name, next, false);
  };

  const symbol = (
    <Slot
      id="t-symbol"
      label="symbol"
      t={{ type: "text" }}
      value={spec["symbol"]}
      width={76}
      onChange={(v) => onChange(withKey(spec, "symbol", typeof v === "string" ? v.toUpperCase() : v))}
    />
  );
  const ex = obj(spec["expiry"]);
  const ek = String(ex["kind"] ?? Object.keys(schema.expiry)[0] ?? "dte");
  const ew = W.expiry[ek];
  const skip = obj(spec["skip_events"]);
  const skipping = list(skip["labels"]).map(String);
  const setSkip = (next: string[]) => onChange(withKey(spec, "skip_events", next.length ? { ...skip, labels: next } : undefined));
  const weekdays = list(spec["weekdays"]).map(Number);
  const far = spec["far_expiry"] !== undefined || legs.some((l) => l["expiry"] === "far");

  return (
    <>
      <div className="b-split">
        <nav className="b-rail" aria-label="Strategies">
          <div className="b-rail-h">Strategy · fills in the legs</div>
          {ss &&
            W.strategy_groups.map((g) => {
              const items = Object.entries(ss.strategies).filter(([k]) => (W.strategies[k]?.group ?? "Custom") === g);
              if (items.length === 0) return null;
              return (
                <div key={g} role="group" aria-label={g}>
                  {g !== "Custom" && <div className="b-rail-g">{g}</div>}
                  {items.map(([k, s]) => (
                    <button
                      key={k}
                      type="button"
                      className={`b-rail-i${name === k ? " is-on" : ""}${k === "custom" ? " is-custom" : ""}`}
                      aria-pressed={name === k}
                      disabled={!s.available}
                      title={s.available ? s.about : (s.why ?? undefined)}
                      onClick={() => pick(k)}
                    >
                      {s.label}
                      {!s.available && <small>{s.why}</small>}
                    </button>
                  ))}
                </div>
              );
            })}
        </nav>
        <div className="b-main">
          <div className="b-head">
            <h2>{def?.label ?? "Custom"}</h2>
            {name !== "custom" && <span className="chip chip-warn">a starting point, not a recommendation</span>}
          </div>
          {def?.about && <p className="b-sub">{def.about}</p>}
          {confirm && ss?.strategies[confirm] && (
            <div className="b-confirm" role="alert">
              <span>The legs below are your own. Replace them with {ss.strategies[confirm]!.label}?</span>
              <button type="button" className="btn btn-primary" onClick={() => void apply(confirm, strategyStart(ss, confirm), confirm !== strategy?.name)}>
                Replace them
              </button>
              <button type="button" className="btn" onClick={() => setConfirm(null)}>
                Keep my legs
              </button>
            </div>
          )}
          <Row lead="Trade">
            {strategy && sw ? (
              edited ? (
                <>
                  <span className="word b-greyed">
                    <Marked segs={strategySegs(W, strategy.name, strategy.settings, spec["symbol"])} />
                  </span>
                  <button type="button" className="btn btn-ghost" onClick={() => setConfirm(strategy.name)}>
                    Rebuild from {def?.label}
                  </button>
                </>
              ) : (
                <Line
                  template={sw.sentence}
                  control={(slot) =>
                    slot === "symbol" ? (
                      symbol
                    ) : (
                      <Slot
                        id={`t-${slot}`}
                        label={slot.replaceAll("_", " ")}
                        t={settingType(schema, slot)}
                        w={{ ...sw, values: { ...W.strategy_values, ...(sw.values ?? {}) } }}
                        wordKey={slot}
                        value={strategy.settings[slot]}
                        onChange={(v) => setSetting(slot, v)}
                        width={60}
                      />
                    )
                  }
                />
              )
            ) : (
              <Line template={W.strategies["custom"]?.sentence ?? "{symbol}"} control={() => symbol} />
            )}
            <Tip id="sym" open={tips.open} onToggle={tips.toggle} label="What is the underlying?" />
          </Row>
          <TipText id="sym" open={tips.open} />
          {problem && (
            <p className="error" role="alert">
              {problem}
            </p>
          )}
          <Row lead="Expiring">
            <select
              className="ctl"
              aria-label="expiry rule"
              value={ek}
              onChange={(e) => {
                const k = e.currentTarget.value;
                const keys = schema.expiry[k] ?? {};
                onChange(withKey(spec, "expiry", { kind: k, ...Object.fromEntries(Object.entries(ex).filter(([x]) => x in keys)) }));
              }}
            >
              {Object.keys(schema.expiry).map((k) => (
                <option key={k} value={k}>
                  {W.expiry[k]?.label ?? k}
                </option>
              ))}
            </select>
            {ew && (
              <Line
                template={without(ew.sentence, "Expiring", ew.label ?? "")}
                control={(slot) => (
                  <Slot
                    id={`t-exp-${slot}`}
                    label={slot}
                    t={schema.types?.expiry[slot]}
                    w={ew}
                    value={ex[slot]}
                    width={56}
                    onChange={(v) => onChange(withKey(spec, "expiry", withKey(ex, slot, v)))}
                  />
                )}
              />
            )}
            <Tip id="exp" open={tips.open} onToggle={tips.toggle} label="What is the expiry?" />
          </Row>
          <TipText id="exp" open={tips.open} />
          <Row lead="On">
            {schema.weekdays.map((d) => (
              <button
                key={d}
                type="button"
                className={`b-day${weekdays.includes(d) ? " is-on" : ""}`}
                aria-pressed={weekdays.includes(d)}
                onClick={() => onChange(withKey(spec, "weekdays", (weekdays.includes(d) ? weekdays.filter((x) => x !== d) : [...weekdays, d]).sort()))}
              >
                {DAYS[d]}
              </button>
            ))}
          </Row>
          <Row lead="Skip">
            {skipping.map((l) => (
              <span key={l} className="b-tag" title={schema.event_about?.[l]?.when}>
                {eventName(schema, l)}
                <button type="button" aria-label={`Stop skipping ${eventName(schema, l)}`} onClick={() => setSkip(skipping.filter((x) => x !== l))}>
                  ×
                </button>
              </span>
            ))}
            <select className="ctl b-add" aria-label="Add an event to skip" value="" onChange={(e) => e.currentTarget.value && setSkip([...skipping, e.currentTarget.value])}>
              <option value="">+ Add an event</option>
              {[...schema.event_labels]
                .filter((l) => !skipping.includes(l))
                .sort((a, b) => eventName(schema, a).localeCompare(eventName(schema, b)))
                .map((l) => (
                  <option key={l} value={l}>
                    {eventName(schema, l)}
                  </option>
                ))}
            </select>
            <Tip id="ev" open={tips.open} onToggle={tips.toggle} label="Why skip an event?" />
          </Row>
          <TipText id="ev" open={tips.open} />
          <More
            title="More expiry options"
            id="t-more"
            items={Object.entries(ew?.more ?? {}).map(([k, phrase]) => ({
              key: k,
              phrase,
              t: schema.types?.expiry[k],
              w: ew,
              value: ex[k],
              set: (v: unknown) => onChange(withKey(spec, "expiry", withKey(ex, k, v))),
            }))}
            extra={
              <>
                {schema.far_expiry && (
                  <div className="b-opt b-far">
                    <FarExpiry schema={schema} spec={spec} onChange={onChange} />
                  </div>
                )}
                <EventKey schema={schema} />
              </>
            }
          />
        </div>
      </div>

      <div className="b-legs">
        <div className="b-legs-h">
          <h3>The legs it makes</h3>
          {SECTION_HELP["structure"] && <CardHelpButton cardTitle="The legs" help={SECTION_HELP["structure"]} />}
          <span className="b-gap" />
          <button type="button" className="btn" onClick={() => setEditing(!editing)}>
            {editing ? "Done editing" : "Edit legs"}
          </button>
        </div>
        {editing && strategy && <p className="b-note">Editing makes this Custom (from {def?.label}).</p>}
        {editing ? <LegFields schema={schema} spec={spec} onChange={onChange} /> : <LegTable schema={schema} W={W} legs={legs} far={far} />}
      </div>
      {payoff.empty ? (
        <p className="muted">Add legs to see the trade's shape and where it lands on the chain.</p>
      ) : (payoff.data ?? payoff.last) ? (
        <TradeShapeView p={(payoff.data ?? payoff.last)!} stale={payoff.stale} />
      ) : (
        <p className="muted">Placing the legs…</p>
      )}
    </>
  );
}

// --------------------------------------------------------------------------- 2 entry
/** The signal kinds by the module's group, in the order each group first appears. */
function signalGroups(W: BuilderWords, kinds: string[]): Array<[string, string[]]> {
  const out = new Map<string, string[]>();
  for (const k of kinds) {
    const g = W.signals[k]?.group ?? "Other";
    out.set(g, [...(out.get(g) ?? []), k]);
  }
  return [...out.entries()];
}

/** An indicator (or a number) as a pill: its choice, then its own line with a box for each setting. */
function SourceControl({ W, schema, value, onChange, id }: { W: BuilderWords; schema: BuilderSchema; value: unknown; onChange: (v: unknown) => void; id: string }) {
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
function SignalControls({ W, schema, sig, onChange, id }: { W: BuilderWords; schema: BuilderSchema; sig: Obj; onChange: (s: Obj) => void; id: string }) {
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
function WithinControl({ w, value, onChange }: { w: BuilderChoiceWords; value: unknown; onChange: (v: unknown) => void }) {
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

function EntrySection({
  schema,
  W,
  spec,
  onChange,
  preset,
  setPreset,
  tips,
}: {
  schema: BuilderSchema;
  W: BuilderWords;
  spec: Spec;
  onChange: (s: Spec) => void;
  preset: Preset | null;
  setPreset: (p: Preset | null) => void;
  tips: ReturnType<typeof useTips>;
}) {
  const specRef = useRef(spec);
  specRef.current = spec;
  const [confirm, setConfirm] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const eps = schema.entry_presets;
  const entry = obj(spec["entry"]);
  const kind = String(entry["kind"] ?? "time");
  const ew = W.entry[kind];
  const et = schema.types?.entry[kind];
  const edited = preset === null ? spec["entry"] !== undefined : JSON.stringify(spec["entry"] ?? null) !== preset.entry;
  const name = edited ? "custom" : (preset?.name ?? "custom");
  const def = eps?.entries[name];
  const setEntry = (next: Obj) => onChange(withKey(spec, "entry", next));

  const apply = async (k: string) => {
    if (!eps) return;
    const r = await builderEntry(k, presetStart(eps, eps.entries[k]!), []);
    if (r.error) return setProblem(r.error);
    if (r.problems?.length) return setProblem(r.problems.join("; "));
    setProblem(null);
    const cur = specRef.current;
    const gates = withFilterGates(cur["gates"], preset?.gates ?? [], r.gates ?? []);
    onChange(withKey({ ...cur, entry: r.entry ?? cur["entry"] }, "gates", gates.length ? gates : undefined));
    setPreset({ name: k, entry: JSON.stringify(r.entry ?? null), gates: (r.gates ?? []).map((g) => JSON.stringify(g)) });
    setConfirm(null);
  };
  const pick = (k: string) => {
    if (edited && confirm !== k) return setConfirm(k);
    void apply(k);
  };

  const trig = obj(entry["trigger"]);
  const form = "at" in trig ? "at" : "signal";
  const tw = W.trigger[form];
  const sig = obj(trig["signal"]);
  const setTrig = (next: Obj) => setEntry({ ...entry, trigger: next });
  const setSig = (next: Obj) => setTrig({ ...trig, signal: next });
  const only = list(sig["only_when"]).map(obj);
  const sigW = W.signals[String(sig["kind"] ?? "")];

  const items: MoreItem[] = [];
  if (kind === "trigger" && form === "signal") {
    for (const r of schema.signal_rules.filter((x) => x !== "only_when")) {
      const rw = W.signal_rules[r];
      if (rw) items.push({ key: r, phrase: rw.sentence, t: schema.types?.signals.rules[r], w: rw, value: sig[r], set: (v) => setSig(withKey(sig, r, v)) });
    }
    for (const [k, phrase] of Object.entries(sigW?.more ?? {}))
      items.push({ key: k, phrase, t: schema.types?.signals.kinds[String(sig["kind"])]?.[k], w: sigW, value: sig[k], set: (v) => setSig(withKey(sig, k, v)) });
    for (const [k, phrase] of Object.entries(tw?.more ?? {}))
      items.push({ key: `trigger-${k}`, phrase, t: schema.types?.trigger[k], w: tw, value: trig[k], set: (v) => setTrig(withKey(trig, k, v)) });
  }
  for (const [k, phrase] of Object.entries(ew?.more ?? {})) items.push({ key: `entry-${k}`, phrase, t: et?.[k], w: ew, value: entry[k], set: (v) => setEntry(withKey(entry, k, v)) });

  return (
    <div className="b-split">
      {eps && (
        <nav className="b-rail" aria-label="Entry presets">
          <div className="b-rail-h">Entry preset · fills in the entry</div>
          {grouped(eps.entries).map(([g, ps]) => (
            <div key={g} role="group" aria-label={g}>
              {g !== "Custom" && <div className="b-rail-g">{g}</div>}
              {ps.map(([k, p]) => (
                <button
                  key={k}
                  type="button"
                  className={`b-rail-i${name === k ? " is-on" : ""}${k === "custom" ? " is-custom" : ""}`}
                  aria-pressed={name === k}
                  title={p.about}
                  onClick={() => pick(k)}
                >
                  {p.label}
                </button>
              ))}
            </div>
          ))}
        </nav>
      )}
      <div className="b-main">
        <div className="b-head">
          <h2>{def?.label ?? "Custom"}</h2>
          {def?.suits && <span className="chip">suits: {def.suits}</span>}
          {name !== "custom" && <span className="chip chip-warn">a starting point, not a tested edge</span>}
        </div>
        {def?.about && <p className="b-sub">{def.about}</p>}
        {confirm && eps?.entries[confirm] && (
          <div className="b-confirm" role="alert">
            <span>The entry below is your own. Replace it with {eps.entries[confirm]!.label}?</span>
            <button type="button" className="btn btn-primary" onClick={() => void apply(confirm)}>
              Replace it
            </button>
            <button type="button" className="btn" onClick={() => setConfirm(null)}>
              Keep my entry
            </button>
          </div>
        )}
        {problem && (
          <p className="error" role="alert">
            {problem}
          </p>
        )}
        <Row lead="Enter">
          <select className="ctl" aria-label="how it enters" value={kind} onChange={(e) => setEntry({ kind: e.currentTarget.value })}>
            {Object.keys(schema.entry).map((k) => (
              <option key={k} value={k}>
                {W.entry[k]?.label ?? k}
              </option>
            ))}
          </select>
          {kind === "trigger" ? (
            <select
              className="ctl"
              aria-label="fires"
              value={form}
              onChange={(e) => setTrig(e.currentTarget.value === "at" ? { at: "", within: 5 } : { signal: {}, window: {}, within: null })}
            >
              {Object.keys(schema.trigger_forms).map((f) => (
                <option key={f} value={f}>
                  {W.trigger[f]?.label ?? f}
                </option>
              ))}
            </select>
          ) : (
            ew && (
              <Line
                template={without(ew.sentence, "Enter")}
                control={(slot) => (
                  <Slot
                    id={`e-${slot}`}
                    label={slot.replaceAll("_", " ")}
                    t={et?.[slot] ?? (slot === "direction" ? { type: "enum", options: schema.orb_directions } : undefined)}
                    w={ew}
                    value={entry[slot]}
                    width={60}
                    onChange={(v) => setEntry(withKey(entry, slot, v))}
                  />
                )}
              />
            )
          )}
          <Tip id="kind" open={tips.open} onToggle={tips.toggle} label="Which kind of entry?" />
        </Row>
        <TipText id="kind" open={tips.open} />
        {kind === "trigger" && form === "signal" && tw && (
          <>
            <Row lead="When">
              <SignalControls W={W} schema={schema} sig={sig} id="e-sig" onChange={setSig} />
            </Row>
            <Row lead="Between">
              <Line
                template={without(tw.sentence, "when {signal},", "between")}
                control={(slot) =>
                  slot === "within" ? (
                    <WithinControl w={tw} value={trig["within"]} onChange={(v) => setTrig({ ...trig, within: v })} />
                  ) : (
                    <Slot
                      id={`e-${slot}`}
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
                  id={`e-only-${i}`}
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
        {kind === "trigger" && form === "at" && tw && (
          <Row lead="At">
            <Line
              template={without(tw.sentence, "at")}
              control={(slot) => <Slot id={`e-at-${slot}`} label={slot} t={schema.types?.trigger[slot]} value={trig[slot]} width={56} onChange={(v) => setTrig(withKey(trig, slot, v))} />}
            />
          </Row>
        )}
        {!holdsCloseSnapshot(entry) && (
          <p className="muted b-cad">
            This time misses the 15:59 chain our monthly history holds.
            <Tip id="cad" open={tips.open} onToggle={tips.toggle} label="Which entry times can be backtested?" />
          </p>
        )}
        <TipText id="cad" open={tips.open} />
        <More title="More options" id="e-more" items={items} />
        <details className="b-more">
          <summary>
            Edit as rule <span className="muted">the entry as the engine reads it</span>
          </summary>
          <textarea
            key={JSON.stringify(entry)}
            className="builder-editor-text"
            aria-label="the entry as JSON"
            spellCheck={false}
            rows={Math.min(16, JSON.stringify(entry, null, 2).split("\n").length + 1)}
            defaultValue={JSON.stringify(entry, null, 2)}
            onBlur={(e) => {
              const el = e.currentTarget;
              try {
                const v = JSON.parse(el.value) as unknown;
                if (typeof v !== "object" || v === null || Array.isArray(v)) throw new Error("an object");
                el.setCustomValidity("");
                if (JSON.stringify(v) !== JSON.stringify(entry)) setEntry(v as Obj);
              } catch {
                el.setCustomValidity("The entry is a JSON object");
                el.reportValidity();
              }
            }}
          />
        </details>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- 5 size and limits
function SizeSection({ schema, W, spec, onChange, tips }: { schema: BuilderSchema; W: BuilderWords; spec: Spec; onChange: (s: Spec) => void; tips: ReturnType<typeof useTips> }) {
  const size = obj(spec["size"]);
  const limits = obj(spec["limits"]);
  const [chosen, setChosen] = useState<string | null>(null);
  const kind = Object.keys(schema.sizes).find((k) => k in size) ?? chosen ?? "contracts";
  const sw = W.sizes[kind];
  const st = schema.types?.size;
  const setSize = (next: Obj) => onChange(withKey(spec, "size", next));
  const setLimit = (k: string, v: unknown) => onChange(withKey(spec, "limits", withKey(limits, k, v)));
  const prog = size["progression"] === undefined ? null : obj(size["progression"]);
  const items: MoreItem[] = Object.entries(sw?.more ?? {}).map(([k, phrase]) => ({ key: k, phrase, t: st?.[k], w: sw, value: size[k], set: (v) => setSize(withKey(size, k, v)) }));
  for (const k of ["every_days", "one_per_expiry"].filter((x) => schema.limits.includes(x))) {
    const lw = W.limits[k];
    if (lw) items.push({ key: k, phrase: lw.sentence, t: schema.types?.limits[k], w: lw, value: limits[k], set: (v) => setLimit(k, v) });
  }
  const limitLine = (k: string) => {
    const lw = W.limits[k];
    return lw ? <Line template={lw.sentence} control={() => <Slot id={`s-${k}`} label={k.replaceAll("_", " ")} t={schema.types?.limits[k]} value={limits[k]} width={52} onChange={(v) => setLimit(k, v)} />} /> : null;
  };
  const progType = (k: string): BuilderFieldType =>
    k === "after" ? { type: "enum", options: Object.keys(W.progression.values?.["after"] ?? {}) } : k === "step" ? { type: "int", or: ["double"] } : { type: "int", min: 1 };
  return (
    <>
      <Row lead="Size">
        <select
          className="ctl"
          aria-label="size by"
          value={kind}
          onChange={(e) => {
            setChosen(e.currentTarget.value);
            setSize(withKey({}, "progression", size["progression"]));
          }}
        >
          {Object.keys(schema.sizes).map((k) => (
            <option key={k} value={k}>
              {W.sizes[k]?.label ?? k}
            </option>
          ))}
        </select>
        {sw && (
          <Line
            template={without(sw.sentence, "Trade")}
            control={(slot) => <Slot id={`s-${slot}`} label={slot.replaceAll("_", " ")} t={st?.[slot]} value={size[slot]} width={72} onChange={(v) => setSize(withKey(size, slot, v))} />}
          />
        )}
        <Tip id="size" open={tips.open} onToggle={tips.toggle} label="How big should it be?" />
      </Row>
      <TipText id="size" open={tips.open} />
      <Row lead="Limits">
        {limitLine("max_open")}
        <span className="word">·</span>
        {limitLine("per_day")}
        <Tip id="lim" open={tips.open} onToggle={tips.toggle} label="Why must it state limits?" />
      </Row>
      <TipText id="lim" open={tips.open} />
      <More
        title="More options"
        id="s-more"
        items={items}
        extra={
          <div className="b-opt">
            {prog ? (
              <>
                <Line
                  template={W.progression.sentence}
                  control={(slot) => <Slot id={`s-prog-${slot}`} label={slot} t={progType(slot)} w={W.progression} value={prog[slot]} width={60} onChange={(v) => setSize(withKey(size, "progression", withKey(prog, slot, v)))} />}
                />
                <button type="button" className="b-x" aria-label="Remove the size step" onClick={() => setSize(withKey(size, "progression", undefined))}>
                  ×
                </button>
              </>
            ) : (
              <button type="button" className="btn btn-ghost" title={W.progression.about} onClick={() => setSize(withKey(size, "progression", {}))}>
                + {W.progression.label}
              </button>
            )}
          </div>
        }
      />
    </>
  );
}

// --------------------------------------------------------------------------- the form
export function BuilderForm({
  schema,
  spec,
  labels,
  onChange,
  readOnly = false,
  open,
  onOpen,
  strategy,
  setStrategy,
  preset,
  setPreset,
  payoff,
}: {
  schema: BuilderSchema;
  spec: Spec;
  labels: Record<string, BuilderBlock["state"]>;
  onChange: (next: Spec) => void;
  /** A saved automation: every line shown and none changeable. */
  readOnly?: boolean;
  open: OpenSections;
  onOpen: (next: OpenSections) => void;
  strategy: Strategy | null;
  setStrategy: (s: Strategy | null) => void;
  preset: Preset | null;
  setPreset: (p: Preset | null) => void;
  payoff: Payoff;
}) {
  const tips = useTips();
  const W = schema.words;
  if (!W) return <p className="error">The automations module sends no words for the form: it is older than this page.</p>;
  const said = automationWords(W, schema, spec, strategy);
  const state = (id: SectionId, blocks: readonly string[]): string => {
    if (open[id] && !readOnly) return "editing";
    const b = blocks.map((x) => labels[x]).find((x) => x !== undefined);
    return b ? STATE_WORDS[b] : "";
  };
  const go = (from: SectionId, to: SectionId) => {
    onOpen({ ...open, [from]: false, [to]: true });
    setTimeout(() => document.getElementById(`b-sec-${to}`)?.scrollIntoView({ block: "start", behavior: "smooth" }), 0);
  };
  const toggle = (id: SectionId) => (v: boolean) => onOpen({ ...open, [id]: v });
  const sec = (i: number) => {
    const s = SECTIONS[i]!;
    const next = SECTIONS[i + 1];
    return { id: s.id, n: s.n, name: s.name, help: s.help, state: state(s.id, s.blocks), open: open[s.id], onToggle: toggle(s.id), readOnly, next: next?.name, onNext: next ? () => go(s.id, next.id) : undefined };
  };
  const tradeLine: Seg[] = [...said.trade, { t: ", " }, ...lower(said.expiry), ...(said.skip.length ? [{ t: " · skip " }, { t: said.skip.join(", "), v: true }] : [])];
  return (
    <section className="b-form" aria-label="The automation, section by section">
      <Sec {...sec(0)} line={tradeLine}>
        <TradeSection schema={schema} W={W} spec={spec} onChange={onChange} strategy={strategy} setStrategy={setStrategy} payoff={payoff} tips={tips} />
      </Sec>
      <Sec {...sec(1)} line={said.entry}>
        <EntrySection schema={schema} W={W} spec={spec} onChange={onChange} preset={preset} setPreset={setPreset} tips={tips} />
      </Sec>
      <Sec {...sec(2)} line={said.filters.length ? joined(said.filters, " · ") : [{ t: "none · optional" }]}>
        <p className="b-sub">Every filter must pass. Market and chain filters are read before the legs are chosen; price filters after.</p>
        <GateFields schema={schema} spec={spec} onChange={onChange} stages={["signal", "chain"]} prefix="f-gate" />
        <h3 className="b-h3">The trade's price · read once the legs are chosen</h3>
        <GateFields schema={schema} spec={spec} onChange={onChange} stages={["structure"]} prefix="f-sgate" />
      </Sec>
      <Sec {...sec(3)} line={said.exits.length ? joined(said.exits, " · ") : [{ t: "held to expiry" }]}>
        <p className="b-sub">It closes at the first exit reached; none set holds it to expiry.</p>
        <ExitFields schema={schema} spec={spec} onChange={onChange} />
      </Sec>
      <Sec {...sec(4)} line={[...said.size, ...(said.limits.length ? [{ t: " · " }, ...joined(said.limits, " · ")] : [])]}>
        <SizeSection schema={schema} W={W} spec={spec} onChange={onChange} tips={tips} />
        <p className="muted">Fills are paper's (mid with the suite's slippage model) and fees are core's: neither is set here. P&amp;L is net.</p>
      </Sec>
    </section>
  );
}
