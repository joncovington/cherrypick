/**
 * How a fly position's structure is named on screen.
 *
 * One function because there were two, and they had already drifted: the open-positions table knew
 * about `bwb` and the trade log did not, so the same position was a "bwb put" on one card and a
 * "short put" on the other. Neither knew about `long_vertical` at all.
 *
 * The drift is not really the bug, though — the shape of the old expression is. Both ended in a
 * default branch that ASSERTED `short ${side}`, so every kind nobody had thought about was rendered
 * as a short vertical rather than as itself. That is how 27 settled `long_vertical` rows came to be
 * labelled as their own opposite: not a wrong mapping, but a default that answered confidently for
 * inputs it had never seen. A new kind added to the module would have inherited the same lie.
 *
 * So the default here returns the raw kind. An unfamiliar structure shows up as an unfamiliar word,
 * which reads as "something new" rather than quietly as something it is not.
 */
/**
 * The clock time of an entry, read off the stored ISO string rather than through a `Date`.
 *
 * `entry_time` carries the market's own UTC offset, so parsing it and formatting it would re-render
 * a 13:54 SPX entry as 10:54 for a viewer on the west coast — a session-relative fact silently
 * restated in a timezone the session never happened in. Slicing keeps the market clock, which is
 * the only one the entry windows and the module's own buckets are expressed in.
 */
export function clockTime(iso: string | null | undefined): string {
  // Truthiness rather than `=== null`: a server that predates this column omits the field entirely,
  // and `undefined.length` throws where a missing value should simply render as a dash. The server
  // and the web bundle are built and restarted independently, so the two halves disagree for as
  // long as one has restarted and the other has not.
  if (!iso || iso.length < 16) return "—";
  return iso.slice(11, 16);
}

/**
 * Wing width in points, `near/far` when the wing is broken.
 *
 * A symmetric fly records only `wingWidth` and both sides are that wide; a bwb records a wider
 * `farWidth` beside it, and the gap between them IS the trade. Collapsing the pair to one number
 * would describe a 5/10 broken wing as a 5-point fly, which is a different structure with a
 * different risk profile.
 */
export function wingWidth(near: number | null | undefined, far: number | null | undefined): string {
  if (near === null || near === undefined) return "—";
  const n = near.toFixed(0);
  return far === null || far === undefined || far === near ? n : `${n}/${far.toFixed(0)}`;
}

export function structureLabel(kind: string | null, side: string | null): string {
  const s = side ?? "";
  switch (kind) {
    case "fly":
      return "fly";
    case "iron_fly":
      return "iron fly";
    case "bwb":
      return s === "" ? "bwb" : `bwb ${s}`;
    case "short_vertical":
      return s === "" ? "short vertical" : `short ${s}`;
    case "long_vertical":
      return s === "" ? "long vertical" : `long ${s}`;
    default:
      return kind ?? "—";
  }
}
