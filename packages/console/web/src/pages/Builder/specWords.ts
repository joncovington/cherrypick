import type { BuilderSchema, BuilderWords } from "@console/shared";
import { legPlain } from "./legWords";
import { say, type Seg } from "./sentence";

/**
 * An automation read in the module's own sentences (`words.py`): each section's one line, and the
 * summary paragraph made of them. The page adds only the joins between sentences.
 */

type Obj = Record<string, unknown>;

export const obj = (v: unknown): Obj => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Obj) : {});
export const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

const t = (text: string): Seg => ({ t: text });

/** Join sentences with `sep`, and `last` before the final one ("a, b and c"). */
export function joined(parts: Seg[][], sep = ", ", last = sep): Seg[] {
  return parts.flatMap((p, i) => (i === 0 ? p : [t(i === parts.length - 1 ? last : sep), ...p]));
}

/** A sentence with its first letter raised. */
export function capital(segs: Seg[]): Seg[] {
  const i = segs.findIndex((s) => s.t.trim() !== "");
  if (i < 0) return segs;
  const s = segs[i]!;
  const lead = s.t.length - s.t.trimStart().length;
  return [...segs.slice(0, i), { ...s, t: s.t.slice(0, lead) + s.t.charAt(lead).toUpperCase() + s.t.slice(lead + 1) }, ...segs.slice(i + 1)];
}

/** A sentence with its first letter lowered, to run on from another. */
export function lower(segs: Seg[]): Seg[] {
  const i = segs.findIndex((s) => s.t.trim() !== "");
  if (i < 0 || segs[i]!.v) return segs;
  const s = segs[i]!;
  return [...segs.slice(0, i), { ...s, t: s.t.charAt(0).toLowerCase() + s.t.slice(1) }, ...segs.slice(i + 1)];
}

/** An indicator, or the number a signal compares with. */
export function sourceSegs(W: BuilderWords, v: unknown): Seg[] {
  if (typeof v === "number") return [{ t: String(v), v: true }];
  const src = obj(v);
  const w = W.sources[String(src["name"] ?? "")];
  return w ? say(w.sentence, src, w) : [{ t: "…", v: true }];
}

/** A signal: its kind's sentence, its indicators in theirs. */
export function signalSegs(W: BuilderWords, schema: BuilderSchema, sig: Obj): Seg[] {
  const kind = String(sig["kind"] ?? "");
  const w = W.signals[kind];
  if (!w) return [{ t: "a signal", v: true }];
  const types = schema.types?.signals.kinds[kind];
  return say(w.sentence, sig, w, {
    nested: (slot, v) => (slot === "a" || slot === "b" ? sourceSegs(W, v) : null),
    dflt: (slot) => types?.[slot]?.default,
  });
}

/** A trigger: a fixed time, or a signal's firing in a window. */
export function triggerSegs(W: BuilderWords, schema: BuilderSchema, trig: Obj): Seg[] {
  const form = "at" in trig ? "at" : "signal";
  const w = W.trigger[form]!;
  return say(w.sentence, trig, w, { nested: (slot, v) => (slot === "signal" ? signalSegs(W, schema, obj(v)) : null) });
}

/** The entry, its conditions after it ("only while …"). */
export function entrySegs(W: BuilderWords, schema: BuilderSchema, entry: Obj): Seg[] {
  const kind = String(entry["kind"] ?? "");
  const w = W.entry[kind];
  if (!w) return [t("Choose how it enters")];
  const types = schema.types?.entry[kind];
  const out = say(w.sentence, entry, w, {
    nested: (slot, v) => (slot === "trigger" ? triggerSegs(W, schema, obj(v)) : null),
    dflt: (slot) => types?.[slot]?.default,
  });
  const only = list(obj(obj(entry["trigger"])["signal"])["only_when"]).map((s) => signalSegs(W, schema, obj(s)));
  const rule = W.signal_rules["only_when"];
  if (only.length && rule) {
    const [before, after] = rule.sentence.split("{only_when}");
    out.push(t(", "), t(before ?? ""), ...joined(only, ", ", " and "), t(after ?? ""));
  }
  return out;
}

/** The expiry rule (or a far one). */
export function expirySegs(W: BuilderWords, schema: BuilderSchema, rule: Obj): Seg[] {
  const kind = String(rule["kind"] ?? "");
  const w = W.expiry[kind];
  return w ? say(w.sentence, rule, w, { dflt: (slot) => schema.types?.expiry[slot]?.default }) : [];
}

/** A strategy and its settings on the underlying. */
export function strategySegs(W: BuilderWords, name: string, settings: Obj, symbol: unknown): Seg[] {
  const w = W.strategies[name];
  if (!w) return [];
  return say(w.sentence, { ...settings, symbol }, { ...w, values: { ...W.strategy_values, ...(w.values ?? {}) } });
}

/** One leg as its order and its strike rule's line: "Sell L1 · Short Put at 0.16 delta". */
export function legSegs(W: BuilderWords, leg: Obj, byName: Record<string, Obj>): Seg[] {
  const strike = obj(leg["strike"]);
  const w = W.strikes[String(strike["rule"] ?? "")];
  const verb = W.strategy_values["direction"]?.[leg["action"] === "STO" || Number(leg["side"]) < 0 ? "short" : "long"] ?? "";
  const place = w
    ? say(w.sentence, strike, w, {
        nested: (slot, v) => {
          if (slot !== "leg" && slot !== "from" && slot !== "width_of") return null;
          const names = Array.isArray(v) ? v : [v];
          return [{ t: names.map((n) => (byName[String(n)] ? legPlain(byName[String(n)]!) : String(n))).join(" and "), v: true }];
        },
      })
    : [];
  // A key the rule's line leaves out but the leg sets (a shift from the price) reads after it.
  const extra = Object.entries(w?.more ?? {})
    .filter(([k]) => strike[k] !== undefined)
    .flatMap(([, phrase]) => [t(", "), ...say(phrase, strike, w)]);
  return [t(`${verb} `), { t: legPlain(leg), v: true }, t(" "), ...place, ...extra];
}

/** A filter gate, a setting left unset read as the gate's own default (RSI's 14). */
export function gateSegs(W: BuilderWords, g: Obj, schema?: BuilderSchema): Seg[] {
  const name = String(g["gate"] ?? "");
  const w = W.gates[name];
  const types = schema?.types?.gates[name];
  return w ? say(w.sentence, g, w, { range: W.range, dflt: (slot) => types?.[slot]?.default }) : [{ t: name || "a filter", v: true }];
}

/** Each exit set, in the order the module words them (profit, loss, price, signal, time, events), then
 *  any it does not word. */
export function exitSegs(W: BuilderWords, schema: BuilderSchema, exit: Obj): Seg[][] {
  const order = [...Object.keys(W.exits), ...schema.exits.filter((k) => !(k in W.exits))];
  return order
    .filter((k) => exit[k] !== undefined)
    .map((k) => {
      const w = W.exits[k];
      if (!w) return [{ t: k, v: true }];
      return say(w.sentence, exit, w, {
        nested: (slot, v) => {
          if (slot === "signal.trigger") return triggerSegs(W, schema, obj(v));
          if (slot === "avoid_events.labels") return [{ t: list(v).map((l) => schema.event_about?.[String(l)]?.name ?? String(l)).join(", "), v: true }];
          return null;
        },
        dflt: (slot) => (slot === "avoid_events.before_close_min" ? 60 : undefined),
      });
    });
}

/** The size rule. */
export function sizeSegs(W: BuilderWords, schema: BuilderSchema, size: Obj): Seg[] {
  const kind = Object.keys(schema.sizes).find((k) => k in size) ?? "contracts";
  const w = W.sizes[kind];
  return w ? say(w.sentence, size, w) : [];
}

/** The limits set, most open first. */
export function limitSegs(W: BuilderWords, limits: Obj): Seg[][] {
  return ["max_open", "per_day", "every_days", "one_per_expiry"]
    .filter((k) => limits[k] !== undefined && limits[k] !== false)
    .map((k) => say(W.limits[k]!.sentence, limits, W.limits[k]));
}

/** The structure's line: the strategy filled in, or each leg's. */
export function tradeSegs(W: BuilderWords, spec: Obj, strategy: { name: string; settings: Obj } | null): Seg[] {
  if (strategy) return strategySegs(W, strategy.name, strategy.settings, spec["symbol"]);
  const legs = list(spec["legs"]).map(obj);
  const byName = Object.fromEntries(legs.map((l) => [String(l["name"] ?? ""), l]));
  const custom = W.strategies["custom"];
  const head = custom ? say(custom.sentence, { symbol: spec["symbol"] }, custom) : [];
  return legs.length ? [...head, t(": "), ...joined(legs.map((l) => legSegs(W, l, byName)), "; ")] : head;
}

/** The automation block by block, each one line of the module's words: what Groups shows beside a
 *  block's backtest label. */
export function blockWords(W: BuilderWords, schema: BuilderSchema, spec: Obj): Record<"structure" | "entry" | "gates" | "exit" | "size", string> {
  const a = automationWords(W, schema, spec, null);
  const text = (segs: Seg[]) => segs.map((s) => s.t).join("");
  return {
    structure: text([...capital(a.trade), t(", "), ...lower(a.expiry), t(".")]),
    entry: text([...capital(a.entry), t("."), ...(a.skip.length ? [t(` Skips ${a.skip.join(", ")}.`)] : [])]),
    gates: a.filters.length ? text(capital(joined(a.filters, "; "))) : "None: every eligible day is entered.",
    exit: a.exits.length ? text(capital(joined(a.exits.map(lower), "; "))) : "None: held to expiry.",
    size: text([...capital(a.size), ...(a.limits.length ? [t(", "), ...joined(a.limits)] : []), t(".")]),
  };
}

/** Every section's line and the paragraph they make. */
export function automationWords(
  W: BuilderWords,
  schema: BuilderSchema,
  spec: Obj,
  strategy: { name: string; settings: Obj } | null,
): { trade: Seg[]; expiry: Seg[]; skip: string[]; entry: Seg[]; filters: Seg[][]; exits: Seg[][]; size: Seg[]; limits: Seg[][]; paragraph: Seg[] } {
  const trade = tradeSegs(W, spec, strategy);
  const expiry = expirySegs(W, schema, obj(spec["expiry"]));
  const skip = list(obj(spec["skip_events"])["labels"]).map((l) => schema.event_about?.[String(l)]?.name ?? String(l));
  const entry = entrySegs(W, schema, obj(spec["entry"]));
  const filters = list(spec["gates"]).map((g) => gateSegs(W, obj(g), schema));
  const exits = exitSegs(W, schema, obj(spec["exit"]));
  const size = sizeSegs(W, schema, obj(spec["size"]));
  const limits = limitSegs(W, obj(spec["limits"]));
  const p: Seg[] = [...capital(trade), t(", "), ...lower(expiry), t(". "), ...capital(entry)];
  if (filters.length) p.push(t(", if "), ...joined(filters, ", ", " and "));
  p.push(t(". "));
  if (exits.length) p.push(...capital(joined(exits.map(lower), "; ")), t(". "));
  p.push(...capital(size), ...(limits.length ? [t(", "), ...joined(limits)] : []), t("."));
  if (skip.length) p.push(t(" Skips "), { t: skip.join(", "), v: true }, t("."));
  return { trade, expiry, skip, entry, filters, exits, size, limits, paragraph: p };
}
