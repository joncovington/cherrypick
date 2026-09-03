/**
 * Colours shared by the hand-SVG chart family (forest, attempts, timeline, journal) -- extracted
 * 2026-09 from three near-identical copies (ForestCard.tsx, MeicForestCard.tsx,
 * TimelineCard.tsx each carried their own `ARM_COLORS`/`SPOT_COLOR`). `Charts.tsx`'s own
 * `SERIES_COLORS` is a separate, general-purpose categorical palette (no brand accent, used by
 * LineChart/BarChart) and stays where it is -- it is not the same list as this one and callers
 * should keep reaching for the one that matches what they're drawing.
 *
 * The 9-colour list is the flies/timeline one; MEIC's old 7-colour copy was its exact prefix, so
 * unifying on the longer list changes nothing for any arm count MEIC has ever run and only means
 * an 8th or 9th profile gets its own colour instead of wrapping back to the first.
 */
export const ARM_COLORS = [
  "#7aa2ff",
  "#43b57a",
  "#d9a13b",
  "#a06bd9",
  "#4fc3d9",
  "#e88a5c",
  "#8a9c4a",
  "#c9628a",
  "#6bd9c4",
];

/** The spot/underlying-price line and its label, wherever a chart draws one. */
export const SPOT_COLOR = "#d9a13b";

/**
 * The attempt-outcome vocabulary, in the order a refusal is most worth knowing about --
 * extracted from `components/Attempts.tsx`'s own `OUTCOMES`, the single source both the ArmRail
 * and the AttemptTimeline read through `COLOR_OF`/`LABEL_OF`. `no_fill` is deliberately its own
 * entry and its own colour: under a fill-based cadence clock an entry that cleared every gate and
 * simply did not fill neither spent the arm's slot nor was refused by a rule, and colouring it as
 * a gate refusal would make the gates look stricter than they are.
 */
export const OUTCOMES = [
  { key: "filled", label: "filled", color: "#43b57a" },
  { key: "cadence_blocked", label: "cadence", color: "#7aa2ff" },
  { key: "sign_rule_blocked", label: "sign rule", color: "#a06bd9" },
  { key: "duplicate_blocked", label: "duplicate", color: "#c9628a" },
  { key: "gate_blocked", label: "gate", color: "#e88a5c" },
  { key: "window_blocked", label: "window", color: "#8a9c4a" },
  { key: "no_candidate", label: "no candidate", color: "#6c7480" },
  { key: "no_fill", label: "no fill", color: "#d9a13b" },
] as const;

export const OUTCOME_COLOR_OF: Record<string, string> = Object.fromEntries(OUTCOMES.map((o) => [o.key, o.color]));
export const OUTCOME_LABEL_OF: Record<string, string> = Object.fromEntries(OUTCOMES.map((o) => [o.key, o.label]));

export const AXIS_MUTED_HEX = "#82878f";
export const AXIS_FONT_FAMILY = "Consolas, monospace";
