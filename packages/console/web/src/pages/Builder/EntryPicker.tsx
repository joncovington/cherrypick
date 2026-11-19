import { useEffect, useRef, useState } from "react";
import type { BuilderEntryPreset, BuilderEntryResult, BuilderEntrySchema } from "@console/shared";
import { builderEntry } from "../../lib/api";

/**
 * The entry presets: one entry that fires, and any number of filters stacked on it, each of which
 * must pass. The automations module turns them into the engine's own `entry` and `gates`
 * (`cli builder-entry`); the page draws the module's lists and settings and builds nothing itself.
 * In the Builder it fills the entry on a button and asks before replacing an entry that was edited;
 * in the wizard (`auto`) it applies as the choice changes. Custom is always a choice.
 */

type Settings = Record<string, string | number>;
export interface Stacked {
  filter: string;
  settings: Settings;
}

/** A preset's or filter's settings as the form starts them: its own defaults over the module's. */
export function startSettings(schema: BuilderEntrySchema, p: BuilderEntryPreset): Settings {
  const out: Settings = {};
  for (const k of p.settings) out[k] = p.defaults[k] ?? schema.settings[k]!.default;
  return out;
}

/** The presets of a table by group, in the module's order. */
export function grouped(table: Record<string, BuilderEntryPreset>): Array<[string, Array<[string, BuilderEntryPreset]>]> {
  const out = new Map<string, Array<[string, BuilderEntryPreset]>>();
  for (const [k, p] of Object.entries(table)) out.set(p.group, [...(out.get(p.group) ?? []), [k, p]]);
  return [...out.entries()];
}

function SettingFields({ schema, keys, values, prefix, onChange }: { schema: BuilderEntrySchema; keys: string[]; values: Settings; prefix: string; onChange: (k: string, v: string | number) => void }) {
  return (
    <>
      {keys.map((k) => {
        const spec = schema.settings[k]!;
        const id = `${prefix}-${k}`;
        return (
          <div key={k} className="builder-f">
            <label className="builder-lbl" htmlFor={id}>
              {spec.label}
            </label>
            {spec.type === "enum" ? (
              <select id={id} className="builder-input" value={String(values[k])} onChange={(e) => onChange(k, e.currentTarget.value)}>
                {(spec.options ?? []).map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            ) : spec.type === "time" ? (
              <input
                id={id}
                key={`${k}-${String(values[k])}`}
                className="builder-input"
                type="text"
                inputMode="numeric"
                placeholder="HH:MM"
                defaultValue={values[k]}
                onBlur={(e) => {
                  const v = e.currentTarget.value.trim();
                  if (v !== values[k]) onChange(k, v);
                }}
              />
            ) : (
              <input
                id={id}
                key={`${k}-${String(values[k])}`}
                className="builder-input"
                type="number"
                inputMode="decimal"
                step={spec.type === "int" ? 1 : "any"}
                min={spec.min}
                max={spec.max}
                defaultValue={values[k]}
                onBlur={(e) => {
                  const n = Number(e.currentTarget.value);
                  if (e.currentTarget.value !== "" && Number.isFinite(n) && n !== values[k]) onChange(k, n);
                }}
              />
            )}
          </div>
        );
      })}
    </>
  );
}

export function EntryPicker({
  schema,
  onFill,
  auto = false,
  edited = false,
  initial = "daily_time",
  initialSettings,
  initialFilters = [],
  applyOnMount = true,
}: {
  schema: BuilderEntrySchema;
  /** The module's answer for the chosen entry and filters. */
  onFill: (r: BuilderEntryResult) => void;
  /** Apply as the choice changes (the wizard), rather than on the button. */
  auto?: boolean;
  /** The entry was changed since it was filled in: the button asks before replacing it. */
  edited?: boolean;
  initial?: string;
  /** The starting preset's settings and filters, where the picker comes back to an earlier choice. */
  initialSettings?: Settings;
  initialFilters?: Stacked[];
  /** In the wizard: apply the starting choice at once, or wait for a change (something of the
   *  person's to keep). */
  applyOnMount?: boolean;
}) {
  const [name, setName] = useState(initial in schema.entries ? initial : Object.keys(schema.entries)[0]!);
  const [settings, setSettings] = useState<Settings>(() => (name === initial && initialSettings ? initialSettings : startSettings(schema, schema.entries[name]!)));
  const [filters, setFilters] = useState<Stacked[]>(() => initialFilters.filter((f) => f.filter in schema.filters));
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const def = schema.entries[name]!;
  const seq = useRef(0);
  const skipFirst = useRef(!applyOnMount);

  const fill = async () => {
    const mine = ++seq.current;
    setBusy(true);
    const r = await builderEntry(name, settings, filters);
    if (mine !== seq.current) return;
    setBusy(false);
    setConfirming(false);
    if (r.error) setProblem(r.error);
    else if (r.problems.length) setProblem(r.problems.join("; "));
    else {
      setProblem(null);
      onFill(r);
    }
  };

  useEffect(() => {
    if (!auto) return;
    if (skipFirst.current) {
      skipFirst.current = false;
      return;
    }
    const t = setTimeout(() => void fill(), 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, name, JSON.stringify(settings), JSON.stringify(filters)]);

  const choose = (k: string) => {
    setName(k);
    setSettings(startSettings(schema, schema.entries[k]!));
    setProblem(null);
    setConfirming(false);
  };
  const addFilter = (k: string) => {
    if (!k || !schema.filters[k]) return;
    setFilters([...filters, { filter: k, settings: startSettings(schema, schema.filters[k]!) }]);
  };
  const setFilter = (i: number, k: string, v: string | number) => setFilters(filters.map((f, j) => (j === i ? { ...f, settings: { ...f.settings, [k]: v } } : f)));

  return (
    <div className="entry-presets">
      <div className="strat" role="group" aria-label="Entry preset">
        <div className="strat-list" role="radiogroup" aria-label="Entry preset">
          <span className="builder-lbl strat-head">entry preset · fills in the entry</span>
          {grouped(schema.entries).map(([group, items]) => (
            <div key={group} className="entry-group" role="presentation">
              {group !== "Custom" && <span className="entry-group-head">{group}</span>}
              {items.map(([k, p]) => (
                <button
                  key={k}
                  type="button"
                  role="radio"
                  aria-checked={name === k}
                  title={p.about}
                  className={`strat-item${name === k ? " is-on" : ""}${k === "custom" ? " is-custom" : ""}`}
                  onClick={() => choose(k)}
                >
                  <span className="strat-name">{p.label}</span>
                  {k === "custom" && <span className="strat-why">any entry</span>}
                </button>
              ))}
            </div>
          ))}
        </div>
        <div className="strat-panel">
          <div className="strat-title">
            <b>{def.label}</b>
            {def.suits && <span className="entry-chip">suits: {def.suits}</span>}
            {name !== "custom" && <span className="entry-chip is-caution">a starting point, not a tested edge</span>}
          </div>
          <span className="muted">{def.about}</span>
          <div className="strat-fields">
            <SettingFields schema={schema} keys={def.settings} values={settings} prefix="entry-p" onChange={(k, v) => setSettings({ ...settings, [k]: v })} />
          </div>

          <div className="entry-filters" role="group" aria-label="Filters">
            <span className="builder-lbl">filters · stack any, all must pass</span>
            {filters.map((f, i) => {
              const p = schema.filters[f.filter]!;
              return (
                <div key={`${f.filter}-${i}`} className="entry-filter">
                  <span className="entry-filter-name" title={p.about}>
                    and · {p.label}
                  </span>
                  <div className="strat-fields">
                    <SettingFields schema={schema} keys={p.settings} values={f.settings} prefix={`entry-f${i}`} onChange={(k, v) => setFilter(i, k, v)} />
                  </div>
                  <button type="button" className="btn builder-remove" aria-label={`Remove filter ${p.label}`} onClick={() => setFilters(filters.filter((_, j) => j !== i))}>
                    ×
                  </button>
                </div>
              );
            })}
            <select className="builder-input entry-add" aria-label="Add filter" value="" onChange={(e) => addFilter(e.currentTarget.value)}>
              <option value="">+ Add filter</option>
              {grouped(schema.filters).map(([group, items]) => (
                <optgroup key={group} label={group}>
                  {items.map(([k, p]) => (
                    <option key={k} value={k}>
                      {p.label}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          {problem && (
            <p className="error" role="alert">
              {problem}
            </p>
          )}
          {!auto && (
            <div className="strat-act">
              {confirming ? (
                <>
                  <span>The entry below was edited. Replace it with {def.label}?</span>
                  <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void fill()}>
                    Replace it
                  </button>
                  <button type="button" className="btn" onClick={() => setConfirming(false)}>
                    Keep my entry
                  </button>
                </>
              ) : (
                <>
                  <button type="button" className="btn btn-primary" disabled={busy} onClick={() => (edited ? setConfirming(true) : void fill())}>
                    Fill in the entry
                  </button>
                  <span className="muted">Sets the entry, and adds the filters to the gates; each stays editable. Filling again replaces the filters it added, unless you edited them.</span>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
