import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { BuilderSchema, BuilderToday, BuilderTypes } from "@console/shared";
import { numberProblem } from "../src/pages/Builder/BuilderField";
import { BuilderForm, EventSkips, entryText, parseValue, showValue, withKey } from "../src/pages/Builder/BuilderForm";
import { plainBlocks } from "../src/pages/Builder/BuilderWizard";
import { legText, TodayView } from "../src/pages/Builder/BuilderToday";
import { SECTION_HELP } from "../src/pages/Builder/BuilderHelp";

const html = (el: React.ReactElement) => renderToString(el).replace(/<!--\s*-->/g, "");

const SCHEMA: BuilderSchema = {
  blocks: { structure: ["symbol", "expiry", "legs"], entry: ["entry", "weekdays", "skip_events"], gates: ["gates"], exit: ["exit"], size: ["size", "limits"] },
  entry: { time: { at: true, window: false }, orb: { minutes: true, direction: true, latest: true, single_shot: false }, trigger: { trigger: true, bar_minutes: false, volume: false } },
  orb_directions: ["above", "below"],
  trigger_forms: { at: { at: true, within: true }, signal: { signal: true, window: true, within: true, daily: false } },
  signals: { gap: ["direction", "pct"], cross: ["a", "b", "direction"] },
  signal_rules: ["confirm", "first_of_day"],
  weekdays: [0, 1, 2, 3, 4],
  event_labels: ["FOMC", "MonthEnd"],
  expiry: { dte: { days: true, tolerance: false } },
  strike_rules: {
    delta: { keys: { target: true, at_most: false }, matches: ["nearest", "above", "below"], default_match: "nearest" },
    from_leg: { keys: { leg: true, points: false, pct: false }, matches: ["exact", "nearest"], default_match: "exact" },
  },
  gates: [
    { gate: "vix_level", stage: "signal", runs: "market", params: [{ name: "min", required: false, default: null }, { name: "max", required: false, default: null }] },
    { gate: "gex_ratio", stage: "chain", runs: "market", params: [{ name: "strikes", required: true, default: null }, { name: "mode", required: false, default: "nearest" }] },
    { gate: "mid_price", stage: "structure", runs: "structure", params: [{ name: "min", required: false, default: null }] },
  ],
  exits: ["profit_target_pct", "touch"],
  limits: ["max_open", "per_day"],
  sizes: { contracts: ["contracts", "progression"], max_risk: ["max_risk", "minimum", "progression"] },
  progression: ["after", "ceiling", "floor", "step"],
};

const SPEC = {
  symbol: "SPX",
  expiry: { kind: "dte", days: 0 },
  entry: { kind: "orb", minutes: 60, direction: "above", latest: "13:00" },
  weekdays: [0, 1, 4],
  skip_events: { labels: ["FOMC"] },
  legs: [
    { name: "short_put", right: "P", side: -1, strike: { rule: "delta", target: 0.35 } },
    { name: "long_put", right: "P", side: 1, strike: { rule: "from_leg", leg: "short_put", points: -20 } },
  ],
  gates: [
    { gate: "gex_ratio", strikes: 16, min: -1.5 },
    { gate: "mid_price", min: 0.15 },
  ],
  size: { contracts: 1 },
  limits: { max_open: 1, per_day: 1 },
  exit: { touch: { points: -10 } },
};

describe("the form's values", () => {
  it("types a field the way it shows: number, flag, JSON, text; blank removes the key", () => {
    expect(parseValue("0.35")).toBe(0.35);
    expect(parseValue("-20")).toBe(-20);
    expect(parseValue("true")).toBe(true);
    expect(parseValue("null")).toBe(null);
    expect(parseValue('{"points": -10}')).toEqual({ points: -10 });
    expect(parseValue("13:00")).toBe("13:00");
    expect(parseValue("  ")).toBeUndefined();
    expect(withKey({ a: 1, b: 2 }, "a", undefined)).toEqual({ b: 2 });
    expect(showValue({ points: -10 })).toBe('{"points":-10}');
  });

  it("says the entry in plain English from its own fields", () => {
    expect(entryText({ kind: "time", at: "10:30" })).toBe("Enter at 10:30, for 5 minutes.");
    expect(entryText(SPEC.entry)).toBe("Enter on the first break above the 60-minute opening range, until 13:00.");
    const sig = { kind: "trigger", bar_minutes: 5, trigger: { signal: { kind: "gap", direction: "up", pct: 0.5 }, window: { open: "09:45", close: "10:00" }, within: null } };
    expect(entryText(sig)).toBe("Enter on the first gap firing (direction up, pct 0.5) on 5-minute bars, between 09:45 and 10:00.");
  });
});

describe("the form", () => {
  const out = html(<BuilderForm schema={SCHEMA} spec={SPEC} labels={{ structure: "backtested", gates: "changed" }} onChange={() => {}} />);

  it("has the five sections, each with its blocks' backtest labels", () => {
    for (const t of ["1 · Underlying and data", "2 · Signal: when to enter", "3 · Gates: the market and the chain", "4 · Structure: legs, then price", "5 · Exits, size, costs"]) {
      expect(out).toContain(t);
    }
    expect(out).toContain('class="chip chip-ok">structure · backtested');
    expect(out).toContain('class="chip chip-warn">gates · changed since its backtest');
    expect(out).toContain('class="chip">exit · not backtested');
  });

  it("offers only the schema's choices and shows the spec's values", () => {
    expect(out).toContain('<option value="orb" selected="">orb</option>');
    expect(out).toContain('value="short_put"');
    expect(out).toContain('<option value="from_leg" selected="">from leg</option>');
    expect(out).toContain('value="-20"');
    expect(out).toContain("Enter on the first break above the 60-minute opening range");
  });

  it("puts market and chain gates in section 3 and price gates in section 4", () => {
    const three = out.slice(out.indexOf("3 · Gates"), out.indexOf("4 · Structure"));
    const four = out.slice(out.indexOf("4 · Structure"), out.indexOf("5 · Exits"));
    expect(three).toContain('<option value="gex_ratio" selected="">gex ratio</option>');
    expect(three).not.toContain('value="mid_price" selected');
    expect(four).toContain('<option value="mid_price" selected="">mid price</option>');
  });

  it("checks the weekdays and events the spec names", () => {
    expect(out).toMatch(/<input type="checkbox" checked=""\/> Mon/);
    expect(out).toMatch(/<input type="checkbox"\/> Wed/);
    expect(out).toMatch(/<input type="checkbox" checked=""\/> FOMC/);
  });
});

describe("typed fields", () => {
  const INT1 = { type: "int" as const, min: 1 };
  const TYPES: BuilderTypes = {
    expiry: { days: { type: "int", min: 0 }, min: { type: "int", min: 0 }, max: { type: "int", min: 0 }, monthly: { type: "bool" } },
    entry: { time: { at: { type: "time" }, window: { ...INT1, default: 5 } }, orb: { minutes: INT1, latest: { type: "time" }, single_shot: { type: "bool", default: true } } },
    trigger: {},
    signals: { kinds: {}, rules: {} },
    strike: { target: { type: "number", above: 0 }, at_most: { type: "number" }, leg: { type: "leg" }, points: { type: "number" }, pct: { type: "number" } },
    gates: { gex_ratio: { strikes: INT1, mode: { type: "enum", options: ["nearest", "above", "below"], default: "nearest" } } },
    exit: { touch: { type: "json" }, profit_target_pct: { type: "number", above: 0 } },
    size: { contracts: INT1 },
    limits: { max_open: INT1, per_day: { type: "int", min: 1, max: 1 } },
  };
  const out = html(<BuilderForm schema={{ ...SCHEMA, types: TYPES }} spec={SPEC} labels={{}} onChange={() => {}} />);

  it("draws a whole number as a bounded number box with its bounds beside the label", () => {
    expect(out).toContain('<input id="f-orb-minutes" class="builder-input" type="number" inputMode="numeric" step="1" min="1" required="" aria-required="true" value="60"/>');
    expect(out).toContain('for="f-limit-per_day">per day<span class="builder-req"> *</span><span class="builder-hint"> · whole, = 1</span>');
    expect(out).toContain('<input id="f-leg-0-target" class="builder-input" type="number" inputMode="decimal" step="any"');
  });

  it("draws a time as a time picker, in Eastern time", () => {
    expect(out).toContain('<input id="f-orb-latest" class="builder-input" type="time" required="" aria-required="true" value="13:00"/>');
    expect(out).toContain('latest<span class="builder-req"> *</span><span class="builder-hint"> · ET</span>');
  });

  it("offers the engine's words, its default first and unwritten", () => {
    expect(out).toContain('<option value="" selected="">default (nearest)</option><option value="nearest">nearest</option>');
    expect(out).toContain('<select id="f-orb-single_shot" class="builder-input"><option value="" selected="">default (yes)</option><option value="true">yes</option><option value="false">no</option></select>');
  });

  it("offers only the legs placed before this one for a leg reference", () => {
    expect(out).toContain('<select id="f-leg-1-leg" class="builder-input" required="" aria-required="true"><option value="">choose…</option><option value="short_put" selected="">short_put</option></select>');
  });

  it("says what is required and what a greyed default means", () => {
    expect(out).toContain('<span class="builder-req">*</span> required. A greyed value in an empty field is the engine&#x27;s default');
  });

  it("refuses a number outside its bounds, in words", () => {
    expect(numberProblem({ type: "int", min: 1 }, "0")).toBe("at least 1");
    expect(numberProblem({ type: "int", min: 1 }, "1.5")).toBe("a whole number");
    expect(numberProblem({ type: "number", above: 0 }, "0")).toBe("more than 0");
    expect(numberProblem({ type: "number", above: 0, max: 4 }, "5")).toBe("at most 4");
    expect(numberProblem({ type: "int", min: 1, or: ["session"] }, "session")).toBeNull();
    expect(numberProblem({ type: "int", min: 1, or: ["session"] }, "x")).toBe("a whole number or session");
    expect(numberProblem({ type: "int", min: 1 }, "")).toBeNull();
  });
});

describe("the event skips", () => {
  const ABOUT = {
    FOMC: { name: "Fed decision (FOMC)", when: "The Federal Reserve's rate decision.", next: "2026-12-09" },
    MonthEnd: { name: "Month end", when: "The last trading day of the month.", next: "2026-11-30" },
    Earnings: { name: "Earnings", when: "The symbol's own earnings report day.", next: null },
  };
  const schema = { ...SCHEMA, event_labels: ["Earnings", "FOMC", "MonthEnd"], event_about: ABOUT };

  it("shows the module's names, sorted by name, and checks the labels the spec names", () => {
    const out = html(<EventSkips schema={schema} on={["FOMC"]} onChange={() => {}} />);
    expect(out).toMatch(/<input type="checkbox" checked=""\/> Fed decision \(FOMC\)/);
    expect(out).toContain('title="The last trading day of the month."');
    expect(out.indexOf("Earnings")).toBeLessThan(out.indexOf("Fed decision")); // by name, not by label
    expect(out.indexOf("Fed decision")).toBeLessThan(out.indexOf("Month end"));
    expect(out).toContain('aria-label="What are these events?"');
    expect(out).not.toContain("builder-event-key"); // the key is closed until asked for
  });

  it("opens a key of what each event is, when it falls and its next day", () => {
    const out = html(<EventSkips schema={schema} on={[]} onChange={() => {}} keyOpen />);
    expect(out).toContain("<td>Fed decision (FOMC)</td><td>The Federal Reserve&#x27;s rate decision.</td><td class=\"num\">Wed 2026-12-09</td>");
    expect(out).toContain("<td>Month end</td><td>The last trading day of the month.</td><td class=\"num\">Mon 2026-11-30</td>");
    expect(out).toContain("<td>Earnings</td><td>The symbol&#x27;s own earnings report day.</td><td class=\"num\">—</td>");
  });

  it("falls back to the bare labels, with no key, from a module that sends no names", () => {
    const out = html(<EventSkips schema={SCHEMA} on={[]} onChange={() => {}} keyOpen />);
    expect(out).toMatch(/<input type="checkbox"\/> FOMC/);
    expect(out).not.toContain("What are these events?");
    expect(out).not.toContain("builder-event-key");
  });

  it("names the skipped events in the wizard's plain English", () => {
    expect(plainBlocks({ ...SPEC, skip_events: { labels: ["FOMC", "MonthEnd"] } }, schema).entry).toContain("skipping Fed decision (FOMC), Month end days");
  });
});

describe("today, live", () => {
  const base: BuilderToday = { name: "mine", state: "would_skip", window: ["10:00", "10:04"], in_window: true, at: "2026-11-17T10:02-05:00" };

  it("lists every check and the one that stops it", () => {
    const out = html(
      <TodayView
        t={{ ...base, reason: "gate:mid_price", checks: [{ check: "gex_ratio", passed: true, value: 1.2 }, { check: "mid_price", passed: false, value: 0.1 }] }}
      />,
    );
    expect(out).toContain("<td>gex ratio</td><td>1.200</td><td class=\"pos\">pass</td>");
    expect(out).toContain("<td>mid price</td><td>0.100</td><td class=\"neg\">fail</td>");
    expect(out).toContain("Would skip now:</strong> gate:mid price");
    expect(out).toContain("Nothing is placed from this page.");
  });

  it("shows the legs it would take in the order chain", () => {
    const entry = { expiry: "2026-11-17", quantity: 1, legs: [{ name: "s", right: "P", side: -1, strike: 5900 }, { name: "l", right: "P", side: 1, strike: 5880 }], prices: { mid: 1.2 }, max_loss: 18.8, max_profit: 1.2 };
    const out = html(<TodayView t={{ ...base, state: "would_enter", reason: "enter", checks: [], entry }} />);
    expect(out).toContain("STO 1 2026-11-17 5900P\nBTO 1 2026-11-17 5880P");
    expect(legText(entry.legs[0]!, 1, "2026-11-17")).toBe("STO 1 2026-11-17 5900P");
  });

  it("says why it could not decide, outside the window", () => {
    const out = html(<TodayView t={{ ...base, state: "no_snapshot", in_window: false, reason: "no_spot", detail: "SPX" }} />);
    expect(out).toContain("No snapshot to decide on: no_spot (SPX).");
    expect(out).toContain("outside it");
  });
});

describe("a leg's match mode", () => {
  it("draws the schema's expiry flags as checkboxes, checked only when set", () => {
    const schema = { ...SCHEMA, expiry: { between: { min: true, max: true, monthly: false } }, expiry_flags: ["monthly"] };
    const on = html(<BuilderForm schema={schema} spec={{ ...SPEC, expiry: { kind: "between", min: 45, max: 60, monthly: true } }} labels={{}} onChange={() => {}} />);
    expect(on).toMatch(/<input id="f-expiry-monthly" class="builder-flag" type="checkbox" checked=""/);
    expect(on).toContain('<input id="f-expiry-min" class="builder-input" required="" aria-required="true" value="45"');
    const off = html(<BuilderForm schema={schema} spec={{ ...SPEC, expiry: { kind: "between", min: 45, max: 60 } }} labels={{}} onChange={() => {}} />);
    expect(off).toMatch(/<input id="f-expiry-monthly" class="builder-flag" type="checkbox"\/>/);
    // Without the schema naming it, a key stays a text field.
    const old = html(<BuilderForm schema={{ ...schema, expiry_flags: undefined }} spec={{ ...SPEC, expiry: { kind: "between", min: 45, max: 60, monthly: true } }} labels={{}} onChange={() => {}} />);
    expect(old).toContain('<input id="f-expiry-monthly" class="builder-input" value="true"');
  });

  it("offers the engine's default when none is set, and says when one is required", () => {
    const out = html(<BuilderForm schema={SCHEMA} spec={SPEC} labels={{}} onChange={() => {}} />);
    expect(out).toContain('<option value="" selected="">default (nearest)</option>');
    expect(out).toContain('<option value="" selected="">default (exact)</option>');
    expect(out).not.toContain("choose…");
  });
});

describe("the section help", () => {
  it("puts a ? on each of the five sections, each opening its own help", () => {
    const out = html(<BuilderForm schema={SCHEMA} spec={SPEC} labels={{}} onChange={() => {}} />);
    for (const t of ["1 · Underlying and data", "2 · Signal: when to enter", "3 · Gates: the market and the chain", "4 · Structure: legs, then price", "5 · Exits, size, costs"]) {
      expect(out).toContain(`aria-label="About ${t}"`);
    }
  });

  it("has a title and sections for every part of the page", () => {
    for (const key of ["underlying", "signal", "gates", "structure", "exits", "today", "runs"]) {
      const h = SECTION_HELP[key]!;
      expect(h.title.length).toBeGreaterThan(5);
      expect(html(<>{h.body}</>)).toContain("<h4>");
    }
    expect(html(<>{SECTION_HELP["structure"]!.body}</>)).toContain("A sold leg needs a bid");
  });
});
