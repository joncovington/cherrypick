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

/** A list's floor before anyone has set one: Live › portfolio's income trades start at $50 (2026-11-06,
 *  the user's standing minimum). Every other list starts off. */
const DEFAULT_FLOOR: Record<string, number> = { "live-income": 50 };

/** A list's floor before anyone has set one (0 is off). */
export function defaultFloor(list: string): number {
  return DEFAULT_FLOOR[list] ?? 0;
}

/** The floor in force for a list: the URL's where it carries one, the remembered preference otherwise,
 *  and the list's default when neither is set (a remembered 0 stays off). */
export function useMinPremium(list: string, override?: string | null): number {
  usePrefsVersion();
  const raw = override ?? getPref(`minPremium.${list}`);
  return raw === null || raw === undefined ? (DEFAULT_FLOOR[list] ?? 0) : premiumFloor(raw);
}

export function PremiumSlider({
  list,
  value,
  title,
  onChange,
  hidden,
}: {
  list: string;
  value: number;
  title: string;
  /** Called with the new floor after the preference is written (a page that keeps a URL copy syncs it). */
  onChange?: (v: number) => void;
  /** How many rows the floor hides, said inside the slider's own chip. Its slot keeps its width at
   *  zero, as the floor's label does from "any" to "$1,000", so nothing beside the slider changes size
   *  while it is dragged and the thumb stays under the pointer (2026-11-06: the row jumped as a
   *  separate "hidden" chip came and went). */
  hidden?: number;
}) {
  return (
    <label className="chip premium-slider" title={title}>
      <span className="premium-slider-value">Premium ≥ {value === 0 ? "any" : `$${value.toLocaleString()}`}</span>
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
      {hidden !== undefined ? (
        <span className="premium-slider-hidden" aria-live="polite">
          {hidden > 0 ? `${hidden.toLocaleString()} hidden` : "none hidden"}
        </span>
      ) : null}
    </label>
  );
}
