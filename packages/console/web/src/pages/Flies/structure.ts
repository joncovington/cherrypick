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
