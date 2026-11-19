import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { BuilderSchema } from "@console/shared";
import { BuilderForm, withStrategy } from "../src/pages/Builder/BuilderForm";
import { startSettings } from "../src/pages/Builder/StrategyPicker";
import { plainBlocks } from "../src/pages/Builder/BuilderWizard";

const html = (el: React.ReactElement) => renderToString(el).replace(/<!--\s*-->/g, "");

const SCHEMA = {
  blocks: { structure: ["symbol", "expiry", "legs"], entry: ["entry"], gates: ["gates"], exit: ["exit"], size: ["size", "limits"] },
  entry: { time: { at: true, window: false } },
  orb_directions: ["above", "below"],
  trigger_forms: {},
  signals: {},
  signal_rules: [],
  weekdays: [0, 1, 2, 3, 4],
  event_labels: [],
  expiry: { at_least: { days: true, monthly: false } },
  far_expiry: { at_least: { days: true, monthly: false }, after_near: { days: true, monthly: false } },
  leg_expiries: ["near", "far"],
  expiry_flags: ["monthly"],
  strike_rules: {
    spot: { keys: { points: false, pct: false }, matches: ["nearest"], default_match: null },
    same_as: { keys: { leg: true }, matches: [], default_match: null },
  },
  gates: [],
  exits: ["close_before_expiry_min"],
  limits: ["max_open", "per_day"],
  sizes: { contracts: ["contracts"] },
  progression: [],
} as unknown as BuilderSchema;

const CAL = {
  symbol: "SPY",
  expiry: { kind: "at_least", days: 25 },
  far_expiry: { kind: "after_near", days: 28, monthly: true },
  entry: { kind: "time", at: "10:00" },
  weekdays: [0, 1, 2, 3, 4],
  legs: [
    { name: "L1", action: "STO", qty: 1, side: "put", strike: { rule: "spot", points: 0, match: "nearest" } },
    { name: "L2", action: "BTO", qty: 1, side: "put", expiry: "far", strike: { rule: "same_as", leg: "L1" } },
  ],
  size: { contracts: 1 },
  limits: { max_open: 1, per_day: 1 },
  exit: { close_before_expiry_min: 15 },
};

describe("the far expiry", () => {
  it("shows the near and far rules, and each leg's expiry column", () => {
    const out = html(<BuilderForm schema={SCHEMA} spec={CAL} labels={{}} onChange={() => {}} />);
    expect(out).toContain(">near expiry<");
    expect(out).toMatch(/<select id="f-far-kind"[^>]*>.*<option value="after_near" selected="">after near<\/option>/);
    expect(out).toContain('id="f-far-days"');
    expect(out).toContain('aria-label="Remove the far expiry"');
    expect(out).toMatch(/<select id="f-leg-0-expiry"[^>]*>.*<option value="near" selected="">Near<\/option>/);
    expect(out).toMatch(/<select id="f-leg-1-expiry"[^>]*>.*<option value="far" selected="">Far<\/option>/);
    expect(out.match(/builder-leg-row has-expiry/g)?.length).toBe(2);
  });

  it("offers to add one, and shows no expiry column, on a one-expiry automation", () => {
    const one = { ...CAL, far_expiry: undefined, legs: [CAL.legs[0]] };
    const out = html(<BuilderForm schema={SCHEMA} spec={one} labels={{}} onChange={() => {}} />);
    expect(out).toContain("+ Far expiry");
    expect(out).toContain(">expiry<");
    expect(out).not.toContain("f-leg-0-expiry");
    expect(out).not.toContain("has-expiry");
    // An older module, with no far rules in its schema, offers none.
    expect(html(<BuilderForm schema={{ ...SCHEMA, far_expiry: undefined }} spec={one} labels={{}} onChange={() => {}} />)).not.toContain("+ Far expiry");
  });
});

describe("the review's words", () => {
  it("name the far expiry and the leg on it", () => {
    const s = plainBlocks(CAL).structure;
    expect(s).toContain("The far expiry: the first listed monthly expiry at least 28 days after it.");
    expect(s).toMatch(/Long Put .* on the far expiry\.$/);
    expect(plainBlocks(CAL).exit).toBe("Close 15 minutes before the near expiry's close.");
    expect(plainBlocks({ ...CAL, far_expiry: undefined, legs: [CAL.legs[0]] }).structure).not.toContain("far");
  });
});

describe("the selector's own defaults", () => {
  it("starts a calendar at the money, and any other strategy at its placing's target", () => {
    const schema = {
      strategies: {
        calendar: { label: "Calendar", about: "", settings: ["right", "far_days", "place_by", "target"], available: true, why: null, defaults: { place_by: "points", target: 0 } },
        option: { label: "Option", about: "", settings: ["place_by", "target"], available: true, why: null },
      },
      settings: {
        right: { type: "enum" as const, options: ["put", "call"], default: "put" },
        far_days: { type: "int" as const, min: 1, max: 400, default: 28 },
        place_by: { type: "enum" as const, options: ["delta", "points"], default: "delta" },
        target: { type: "number" as const, above: 0, default: 0.16 },
      },
      target_defaults: { delta: 0.16, points: 10 },
    };
    expect(startSettings(schema, "calendar")).toEqual({ right: "put", far_days: 28, place_by: "points", target: 0 });
    expect(startSettings(schema, "option")).toEqual({ place_by: "delta", target: 0.16 });
  });
});

describe("a strategy filling the automation", () => {
  const base = { ...CAL, far_expiry: undefined, exit: {} };

  it("sets the far expiry and the close before the near expiry a far leg needs", () => {
    const next = withStrategy(base, { legs: CAL.legs, expiry: CAL.expiry, far_expiry: CAL.far_expiry, spec: { exit: { close_before_expiry_min: 15 } } });
    expect(next["far_expiry"]).toEqual(CAL.far_expiry);
    expect(next["exit"]).toEqual({ close_before_expiry_min: 15 });
    // An automation that already closes before the near expiry keeps its own minutes.
    const own = withStrategy({ ...base, exit: { close_before_expiry_min: 60 } }, { legs: CAL.legs, far_expiry: CAL.far_expiry, spec: { exit: { close_before_expiry_min: 15 } } });
    expect(own["exit"]).toEqual({ close_before_expiry_min: 60 });
  });

  it("leaves no far rule behind when a one-expiry strategy replaces a calendar", () => {
    const next = withStrategy(CAL, { legs: [CAL.legs[0]], expiry: { kind: "at_least", days: 45 } });
    expect("far_expiry" in next).toBe(false);
    expect(next["expiry"]).toEqual({ kind: "at_least", days: 45 });
  });
});
