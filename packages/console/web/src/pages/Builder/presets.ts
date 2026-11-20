import type { BuilderEntryPreset, BuilderEntrySchema, BuilderStrategySchema } from "@console/shared";

/**
 * The strategies' and the entry presets' starting settings, as the Builder sends them to the module
 * (`cli builder-strategy`, `cli builder-entry`), and the presets by group. The module turns them into
 * legs, an expiry and an entry; the page builds nothing itself.
 */

type Settings = Record<string, string | number>;

/** A strategy's settings as a pick starts them: its own defaults over the module's, the target by
 *  `place_by` unless the strategy names its own (a calendar's 0 points: at the money). */
export function strategyStart(schema: BuilderStrategySchema, name: string): Settings {
  const s = schema.strategies[name];
  const own = s?.defaults ?? {};
  const out: Settings = {};
  for (const k of s?.settings ?? []) out[k] = own[k] ?? schema.settings[k]!.default;
  if ("target" in out && !("target" in own)) out["target"] = schema.target_defaults[String(out["place_by"] ?? "delta")] ?? out["target"]!;
  return out;
}

/** A preset's or filter's settings as a pick starts them: its own defaults over the module's. */
export function presetStart(schema: BuilderEntrySchema, p: BuilderEntryPreset): Settings {
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
