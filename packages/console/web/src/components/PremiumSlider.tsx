import { getPref, usePrefsVersion, writePref } from "../lib/prefs";

/**
 * The minimum-premium slider every income and earnings list carries (2026-10-22): one look, one set of
 * stops, and a floor the console remembers per list (a display preference, `minPremium.<list>`), so a
 * standing personal minimum does not have to be set again each visit. A page that also keeps the floor
 * in its URL passes it as `override`; the URL wins while it is there.
 */

/** The slider's stops, whole-contract dollars. 0 is off. Stepped, because most rows' premiums are tens
 *  of dollars and a straight scale to the largest would leave them a few pixels. */
export const PREMIUM_STEPS: readonly number[] = [0, 10, 25, 50, 75, 100, 150, 200, 300, 500, 750, 1000];

/** A value read back onto a stop: anything else (a hand-typed 60, junk) is the nearest stop below. */
export function premiumFloor(raw: unknown): number {
  const v = Number(raw);
  if (raw === null || raw === undefined || raw === "" || !Number.isFinite(v) || v <= 0) return 0;
  return [...PREMIUM_STEPS].reverse().find((s) => s <= v) ?? 0;
}

/** The floor in force for a list: the URL's where it carries one, the remembered preference otherwise. */
export function useMinPremium(list: string, override?: string | null): number {
  usePrefsVersion();
  return premiumFloor(override ?? getPref(`minPremium.${list}`));
}

export function PremiumSlider({
  list,
  value,
  title,
  onChange,
}: {
  list: string;
  value: number;
  title: string;
  /** Called with the new floor after the preference is written (a page that keeps a URL copy syncs it). */
  onChange?: (v: number) => void;
}) {
  return (
    <label className="chip" style={{ display: "inline-flex", alignItems: "center", gap: 6 }} title={title}>
      Premium ≥ {value === 0 ? "any" : `$${value.toLocaleString()}`}
      <input
        type="range"
        aria-label="Minimum premium per contract"
        min={0}
        max={PREMIUM_STEPS.length - 1}
        step={1}
        value={PREMIUM_STEPS.indexOf(value)}
        onChange={(e) => {
          const v = PREMIUM_STEPS[Number(e.target.value)] ?? 0;
          void writePref(`minPremium.${list}`, v);
          onChange?.(v);
        }}
      />
    </label>
  );
}
