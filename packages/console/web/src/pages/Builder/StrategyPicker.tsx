import { useEffect, useRef, useState } from "react";
import type { BuilderStrategyResult, BuilderStrategySchema } from "@console/shared";
import { builderStrategy } from "../../lib/api";

/**
 * The strategy selector: a structure and a few settings, which the automations module turns into legs
 * and an expiry rule (`cli builder-strategy`, the engine's own strike and expiry rules). The page draws
 * the module's list and settings and builds nothing itself. In the Builder it fills the legs on a
 * button and asks before replacing legs that were edited; in the wizard (`auto`) it applies as the
 * choice changes, since there is nothing of the person's to replace yet. Custom is always a choice.
 */

const WORDS: Record<string, string> = {
  short: "Short",
  long: "Long",
  put: "Put",
  call: "Call",
  all: "All",
  weekly: "Weekly",
  monthly: "Monthly",
  delta: "Delta",
  pct: "% OTM",
  sd: "SD",
  points: "Points",
};

const LABELS: Record<string, string> = {
  direction: "direction",
  right: "side",
  dte: "DTE",
  expiries: "expiries",
  place_by: "place by",
  width: "width · points",
  width2: "far wing · points",
  ratio: "sold for each bought",
  far_days: "far · days after near",
};

/** The target's label by how the strikes are placed. */
export function targetLabel(placeBy: string): string {
  return { delta: "target delta", pct: "% from the spot", sd: "standard deviations", points: "points from the spot" }[placeBy] ?? "target";
}

/** A strategy's settings as the form starts them: the strategy's own defaults over the module's, the
 *  target by `place_by` unless the strategy names its own (a calendar's 0 points: at the money). */
export function startSettings(schema: BuilderStrategySchema, name: string): Record<string, string | number> {
  const s = schema.strategies[name];
  const own = s?.defaults ?? {};
  const out: Record<string, string | number> = {};
  for (const k of s?.settings ?? []) out[k] = own[k] ?? schema.settings[k]!.default;
  if ("target" in out && !("target" in own)) out["target"] = schema.target_defaults[String(out["place_by"] ?? "delta")] ?? out["target"]!;
  return out;
}

export function StrategyPicker({
  schema,
  onFill,
  auto = false,
  edited = false,
  initial = "vertical",
  applyOnMount = true,
}: {
  schema: BuilderStrategySchema;
  /** The module's answer for the chosen strategy and settings. */
  onFill: (r: BuilderStrategyResult) => void;
  /** Apply as the choice changes (the wizard), rather than on the button. */
  auto?: boolean;
  /** The legs were changed since they were filled in: the button asks before replacing them. */
  edited?: boolean;
  initial?: string;
  /** In the wizard: apply the starting choice at once (a new automation), or wait for a change (a copy, or
   *  coming back to this step, when there is something of the person's to keep). */
  applyOnMount?: boolean;
}) {
  const [name, setName] = useState(initial in schema.strategies ? initial : Object.keys(schema.strategies)[0]!);
  const [settings, setSettings] = useState<Record<string, string | number>>(() => startSettings(schema, name));
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const def = schema.strategies[name]!;
  const seq = useRef(0);
  const skipFirst = useRef(!applyOnMount);

  const fill = async () => {
    const mine = ++seq.current;
    setBusy(true);
    const r = await builderStrategy(name, settings);
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
    if (!auto || !def.available) return;
    if (skipFirst.current) {
      skipFirst.current = false;
      return;
    }
    const t = setTimeout(() => void fill(), 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, name, JSON.stringify(settings)]);

  const choose = (k: string) => {
    setName(k);
    setSettings(startSettings(schema, k));
    setProblem(null);
    setConfirming(false);
  };
  const set = (k: string, v: string | number) => {
    const next = { ...settings, [k]: v };
    if (k === "place_by") next["target"] = schema.target_defaults[String(v)] ?? next["target"]!;
    setSettings(next);
  };

  return (
    <div className="strat" role="group" aria-label="Strategy">
      <div className="strat-list" role="radiogroup" aria-label="Strategy">
        <span className="builder-lbl strat-head">strategy · fills in the legs</span>
        {Object.entries(schema.strategies).map(([k, s]) => (
          <button
            key={k}
            type="button"
            role="radio"
            aria-checked={name === k}
            disabled={!s.available}
            title={s.available ? s.about : (s.why ?? undefined)}
            className={`strat-item${name === k ? " is-on" : ""}${k === "custom" ? " is-custom" : ""}`}
            onClick={() => choose(k)}
          >
            <span className="strat-name">{s.label}</span>
            {!s.available ? <span className="strat-why">{s.why}</span> : k === "custom" ? <span className="strat-why">any legs</span> : null}
          </button>
        ))}
      </div>
      <div className="strat-panel">
        <div className="strat-title">
          <b>{def.label}</b>
          <span className="muted">{def.about}</span>
        </div>
        <div className="strat-fields">
          {def.settings.map((k) => {
            const spec = schema.settings[k]!;
            const id = `strat-${k}`;
            if (spec.type === "enum") {
              return (
                <div key={k} className="builder-f strat-seg-f">
                  <span className="builder-lbl">{LABELS[k] ?? k}</span>
                  <div className="strat-seg" role="radiogroup" aria-label={LABELS[k] ?? k}>
                    {(spec.options ?? []).map((o) => (
                      <button key={o} type="button" role="radio" aria-checked={settings[k] === o} className={settings[k] === o ? "is-on" : undefined} onClick={() => set(k, o)}>
                        {WORDS[o] ?? o}
                      </button>
                    ))}
                  </div>
                </div>
              );
            }
            const label = k === "target" ? targetLabel(String(settings["place_by"] ?? "delta")) : (LABELS[k] ?? k);
            return (
              <div key={k} className="builder-f">
                <label className="builder-lbl" htmlFor={id}>
                  {label}
                </label>
                <input
                  id={id}
                  key={`${k}-${String(settings[k])}`}
                  className="builder-input"
                  type="number"
                  inputMode="decimal"
                  step={spec.type === "int" ? 1 : "any"}
                  min={spec.min ?? spec.above}
                  max={spec.max}
                  defaultValue={settings[k]}
                  onBlur={(e) => {
                    const n = Number(e.currentTarget.value);
                    if (Number.isFinite(n) && n !== settings[k]) set(k, n);
                  }}
                />
              </div>
            );
          })}
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
                <span>The legs below were edited. Replace them with {def.label}?</span>
                <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void fill()}>
                  Replace them
                </button>
                <button type="button" className="btn" onClick={() => setConfirming(false)}>
                  Keep my legs
                </button>
              </>
            ) : (
              <>
                <button type="button" className="btn btn-primary" disabled={busy || !def.available} onClick={() => (edited ? setConfirming(true) : void fill())}>
                  Fill in the legs
                </button>
                <span className="muted">Sets the legs and the expiry; each stays editable, and + Add leg still adds more. A starting point, never a recommendation.</span>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
