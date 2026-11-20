import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { BuilderSchema, BuilderToday } from "@console/shared";
import FIXTURE from "./fixtures/builderSchema.json";
import { numberProblem } from "../src/pages/Builder/BuilderField";
import { LegFields, parseValue, showValue, withKey } from "../src/pages/Builder/BuilderFields";
import { BuilderForm, type OpenSections, type Strategy } from "../src/pages/Builder/BuilderForm";
import { at, cut, plain, rangeTemplate, say, setAt, valueWords, without } from "../src/pages/Builder/sentence";
import { automationWords, blockWords } from "../src/pages/Builder/specWords";
import { legText, TodayView } from "../src/pages/Builder/BuilderToday";
import { SECTION_HELP } from "../src/pages/Builder/BuilderHelp";
import { holdsCloseSnapshot } from "../src/pages/Builder/builderTips";

const html = (el: React.ReactElement) => renderToString(el).replace(/<!--\s*-->/g, "");

/** The automations module's own schema (`cli builder-schema` as of 2026-11-20, without the shapes). */
const SCHEMA = FIXTURE as unknown as BuilderSchema;
const W = SCHEMA.words!;

const IC = {
  symbol: "SPY",
  expiry: { kind: "between", min: 40, max: 50 },
  entry: {
    kind: "trigger",
    bar_minutes: 5,
    trigger: {
      signal: {
        kind: "cross",
        a: { name: "rsi", n: 2 },
        b: 10,
        direction: "below",
        seed: true,
        only_when: [{ kind: "cross", a: { name: "close" }, b: { name: "sma", n: 200 }, direction: "above", seed: true }],
      },
      window: { open: "15:50", close: "15:59" },
      within: null,
    },
  },
  weekdays: [0, 1, 2, 3, 4],
  skip_events: { labels: ["FOMC"] },
  legs: [
    { name: "L1", action: "STO", qty: 1, side: "put", strike: { rule: "delta", target: 0.16 } },
    { name: "L2", action: "STO", qty: 1, side: "call", strike: { rule: "delta", target: 0.16 } },
    { name: "L3", action: "BTO", qty: 1, side: "put", strike: { rule: "from_leg", leg: "L1", points: -10 } },
    { name: "L4", action: "BTO", qty: 1, side: "call", strike: { rule: "from_leg", leg: "L2", points: 10 } },
  ],
  gates: [{ gate: "vix_level", min: 14, max: 30 }],
  exit: { close_at_dte: 21, stop_credit_multiple: 1, profit_target_pct: 50 },
  size: { contracts: 1 },
  limits: { max_open: 3, per_day: 1 },
};
const STRATEGY: Strategy = {
  name: "iron_condor",
  settings: { direction: "short", dte: 45, expiries: "monthly", place_by: "delta", target: 0.16, width: 10 },
  legs: JSON.stringify(IC.legs),
};
const CLOSED: OpenSections = { trade: false, entry: false, filters: false, exits: false, size: false };
const NO_PAYOFF = { empty: false, data: null, stale: false, last: null };

function form(p: { spec?: Record<string, unknown>; open?: Partial<OpenSections>; readOnly?: boolean; strategy?: Strategy | null; labels?: Record<string, "backtested" | "changed" | "not_backtested">; schema?: BuilderSchema }) {
  return html(
    <BuilderForm
      schema={p.schema ?? SCHEMA}
      spec={p.spec ?? IC}
      labels={p.labels ?? {}}
      onChange={() => {}}
      readOnly={p.readOnly}
      open={{ ...CLOSED, ...p.open }}
      onOpen={() => {}}
      strategy={p.strategy === undefined ? STRATEGY : p.strategy}
      setStrategy={() => {}}
      preset={null}
      setPreset={() => {}}
      payoff={NO_PAYOFF}
    />,
  );
}

describe("a sentence", () => {
  it("cuts a template into its words and its slots", () => {
    expect(cut("at {at} ET, within {within} minutes")).toEqual([{ text: "at " }, { slot: "at" }, { text: " ET, within " }, { slot: "within" }, { text: " minutes" }]);
    expect(cut("{trailing_stop.dollars}")).toEqual([{ slot: "trailing_stop.dollars" }]);
  });

  it("drops a phrase the line already shows beside it, only at a word's end", () => {
    expect(without("Enter at {at} ET", "Enter")).toBe("at {at} ET");
    expect(without("Expiring between {min} and {max} days out", "Expiring", "Between")).toBe("{min} and {max} days out");
    expect(without("atleast {days}", "at")).toBe("atleast {days}");
    expect(without("when {signal}, between {window.open}", "when {signal},", "between")).toBe("{window.open}");
  });

  it("reads a value in its own words: a flag, a null, a number phrase, an option, a list", () => {
    const w = W.trigger["signal"];
    expect(valueWords(w, "within", null)).toBe("any time before the window closes");
    expect(valueWords(w, "within", 5)).toBe("within 5 minutes of the firing");
    expect(valueWords(W.limits["one_per_expiry"], "one_per_expiry", true)).toBe("one position per expiry");
    expect(valueWords(W.signals["change"], "from", "prior_close")).toBe("the previous close");
    expect(valueWords(undefined, "x", "first_bar_close")).toBe("first bar close");
  });

  it("reads a filter's min and max as one band, either side alone", () => {
    expect(rangeTemplate(W.range, { min: 1, max: 2 })).toBe(W.range.both);
    expect(rangeTemplate(W.range, { min: 1 })).toBe(W.range.min);
    expect(rangeTemplate(W.range, { max: 2 })).toBe(W.range.max);
    expect(rangeTemplate(W.range, {})).toBeNull();
    expect(plain(say(W.gates["vix_level"]!.sentence, { min: 14 }, W.gates["vix_level"], { range: W.range }))).toBe("VIX is at least 14");
  });

  it("marks a slot with no value, and reads and writes a nested key", () => {
    expect(say("at {at} ET", {}, undefined)).toEqual([{ t: "at " }, { t: "…", v: true }, { t: " ET" }]);
    expect(at({ window: { open: "09:45" } }, "window.open")).toBe("09:45");
    expect(setAt({ window: { open: "09:45" } }, "window.close", "10:00")).toEqual({ window: { open: "09:45", close: "10:00" } });
    expect(setAt({ window: { open: "09:45" } }, "window.open", undefined)).toEqual({});
  });
});

describe("the automation in words", () => {
  const said = automationWords(W, SCHEMA, IC, STRATEGY);
  const p = plain(said.paragraph);

  it("says the trade by its strategy, its expiry run on", () => {
    expect(p.startsWith("Sell an iron condor on SPY with short strikes at 0.16 delta and wings 10 points wide, expiring between 40 and 50 days out, the nearest.")).toBe(true);
  });

  it("says the entry with its indicators, its window and its condition", () => {
    expect(p).toContain("Enter when RSI(2) crosses below 10, between 15:50 and 15:59 ET, entering any time before the window closes, only while the close crosses above the 200-bar simple average holds");
  });

  it("says the filters, the exits in the module's order, the size and the limits", () => {
    expect(p).toContain(", if VIX is between 14 and 30.");
    expect(p).toContain("Take profit at 50% of the most it can make; stop at a loss of 1× the credit; close with 21 days left.");
    expect(p).toContain("Trade 1 contract each time, at most 3 open at once, at most 1 a day.");
    expect(plain(automationWords(W, SCHEMA, { ...IC, size: { contracts: 2 } }, STRATEGY).size)).toBe("Trade 2 contracts each time");
    expect(p).toContain("Skips Fed decision (FOMC).");
  });

  it("marks every value the person set", () => {
    const marked = said.paragraph.filter((s) => s.v).map((s) => s.t);
    for (const v of ["SPY", "0.16", "10", "40", "50", "15:50", "50", "1", "21", "3"]) expect(marked).toContain(v);
  });

  it("says each leg by its strike rule when no strategy filled them", () => {
    const t = plain(automationWords(W, SCHEMA, IC, null).trade);
    expect(t).toContain("Any legs on SPY: Sell L1 · Short Put at 0.16 delta; Sell L2 · Short Call at 0.16 delta; Buy L3 · Long Put from L1 · Short Put");
  });

  it("gives Groups one line a block in the same words", () => {
    const b = blockWords(W, SCHEMA, IC);
    expect(b.gates).toBe("VIX is between 14 and 30");
    expect(b.exit).toBe("Take profit at 50% of the most it can make; stop at a loss of 1× the credit; close with 21 days left");
    expect(blockWords(W, SCHEMA, { ...IC, gates: [], exit: {} }).exit).toBe("None: held to expiry.");
  });
});

describe("the one-page form", () => {
  it("has the five sections in order, each closed to its line with its block's label", () => {
    const out = form({ labels: { structure: "backtested", gates: "changed" } });
    const names = [...out.matchAll(/<span class="b-sec-name">([^<]+)<\/span>/g)].map((m) => m[1]);
    expect(names).toEqual(["Trade", "Entry", "Filters", "Exits", "Size and limits"]);
    expect(out).toContain('<span class="b-sec-state is-ok">backtested</span>');
    expect(out).toContain('<span class="b-sec-state">changed since its backtest</span>');
    expect(out).not.toMatch(/<details[^>]*open=""/);
    expect(out).toMatch(/b-sec-line"><span class="said">Sell<\/span> an iron condor on <span class="said">SPY<\/span>/);
  });

  it("marks the open section as editing and offers the next one", () => {
    const out = form({ open: { trade: true } });
    expect(out).toMatch(/<details class="b-sec" id="b-sec-trade" open="">/);
    expect(out).toContain('<span class="b-sec-state is-cur">editing</span>');
    expect(out).toContain("Next: <!-- -->Entry".replace("<!-- -->", ""));
  });

  it("lists the strategies under the module's groups, Custom last, the chosen one pressed", () => {
    const out = form({ open: { trade: true } });
    const rail = out.slice(out.indexOf('aria-label="Strategies"'), out.indexOf("b-main"));
    for (const g of ["One option", "Spreads", "Both sides", "Two expiries"]) expect(rail).toContain(`<div class="b-rail-g">${g}</div>`);
    expect(rail).toMatch(/class="b-rail-i is-on" aria-pressed="true"[^>]*>Iron condor/);
    expect(rail.lastIndexOf("is-custom")).toBeGreaterThan(rail.indexOf("Calendar"));
  });

  it("draws the strategy's line with a control in each slot, in the module's words", () => {
    const out = form({ open: { trade: true } });
    expect(out).toMatch(/<select id="t-direction" class="ctl" aria-label="direction">.*<option value="short" selected="">Sell<\/option><option value="long">Buy<\/option>/);
    expect(out).toContain('<input id="t-symbol" class="ctl" style="width:76px" aria-label="symbol" value="SPY"/>');
    expect(out).toMatch(/<span class="word">with short strikes at<\/span><input id="t-target"/);
    expect(out).toMatch(/<option value="pct">% from the price<\/option>/);
  });

  it("greys the strategy's line once the legs are edited, and offers to rebuild", () => {
    const out = form({ open: { trade: true }, strategy: { ...STRATEGY, legs: "[]" } });
    expect(out).toContain("b-greyed");
    expect(out).toContain("Rebuild from <!-- -->Iron condor".replace("<!-- -->", ""));
    expect(out).not.toContain('id="t-direction"');
  });

  it("puts the expiry rule's kind beside its lead and its values in the line", () => {
    const out = form({ open: { trade: true } });
    expect(out).toMatch(/<span class="b-lead">Expiring<\/span><select class="ctl" aria-label="expiry rule">.*<option value="between" selected="">Between<\/option>/);
    expect(out).toContain('<input id="t-exp-min" class="ctl" style="width:56px" type="number" inputMode="numeric" step="1" min="0" aria-label="min" value="40"/>');
    // Unset, the engine's default is shown chosen, once.
    expect(out).toContain('<select id="t-exp-prefer" class="ctl" aria-label="prefer"><option value="first" selected="">nearest</option><option value="last">farthest</option></select>');
  });

  it("shows the weekdays as pressed buttons and the skipped events as tags by name", () => {
    const out = form({ open: { trade: true }, spec: { ...IC, weekdays: [0, 2] } });
    expect(out).toContain('<button type="button" class="b-day is-on" aria-pressed="true">Mon</button><button type="button" class="b-day" aria-pressed="false">Tue</button>');
    expect(out).toMatch(/<span class="b-tag" title="[^"]+">Fed decision \(FOMC\)<button type="button" aria-label="Stop skipping Fed decision \(FOMC\)">×<\/button><\/span>/);
    expect(out).toMatch(/<option value="" selected="">\+ Add an event<\/option>.*<option value="NFP">/);
    expect(out).not.toMatch(/aria-label="Add an event to skip".*<option value="FOMC">/);
  });

  it("shows the legs read-only in columns until Edit legs", () => {
    const out = form({ open: { trade: true } });
    expect(out).toContain("<th>Leg</th><th>Action</th><th>Qty</th><th>Side</th><th>Strike</th><th>Placed by</th><th>From</th><th>Match</th>");
    expect(out).toContain('<td><span class="b-leg-id">L3</span></td><td>Buy</td><td class="num">1</td><td>put</td><td class="num">-10</td><td>From another leg</td><td>L1 · Short Put</td><td class="dim">exact</td>');
    expect(out).toContain(">Edit legs</button>");
    expect(out).not.toContain('id="f-leg-0-action"');
  });

  it("opens straight into editing for an automation with no legs", () => {
    const out = form({ open: { trade: true }, spec: { ...IC, legs: [] }, strategy: null });
    expect(out).toContain(">Done editing</button>");
    expect(out).toContain("+ Add leg (L1)");
    expect(out).toMatch(/<h2>Custom<\/h2>/);
  });

  it("draws the entry as lines: how it enters, the signal, the window, each condition", () => {
    const out = form({ open: { entry: true } });
    expect(out).toMatch(/<select class="ctl" aria-label="how it enters">.*<option value="trigger" selected="">On a trigger<\/option>/);
    expect(out).toMatch(/<span class="b-lead">When<\/span><select class="ctl" aria-label="signal">.*<option value="cross" selected="">Crosses<\/option>/);
    expect(out).toMatch(/<span class="b-pill"><select class="ctl" aria-label="indicator">.*<option value="rsi" selected="">RSI<\/option>/);
    expect(out).toMatch(/<span class="b-lead">Between<\/span><input id="e-window.open"[^>]*value="15:50"/);
    expect(out).toMatch(/<option value="null" selected="">any time before the window closes<\/option>/);
    expect(out.match(/<span class="b-lead">Only if<\/span>/g)?.length).toBe(1);
    expect(out).toContain("+ Add a condition");
  });

  it("puts the rules a line leaves out under More options, and the whole entry under Edit as rule", () => {
    const out = form({ open: { entry: true } });
    expect(out).toContain('<span class="word">held for</span><input id="e-more-confirm"');
    expect(out).toMatch(/<input id="e-more-seed" type="checkbox" checked=""\/> carrying its reading across sessions/);
    expect(out).toMatch(/More options <span class="muted">\d set<\/span>/);
    expect(out).toContain('aria-label="the entry as JSON"');
  });

  it("names a time entry outside the one daily snapshot", () => {
    expect(form({ open: { entry: true }, spec: { ...IC, entry: { kind: "time", at: "10:00" } } })).toContain("misses the 15:59 chain");
    expect(form({ open: { entry: true }, spec: { ...IC, entry: { kind: "time", at: "15:55" } } })).not.toContain("misses the 15:59 chain");
    expect(holdsCloseSnapshot({ kind: "time", at: "15:55" })).toBe(true);
  });

  it("draws the size and the limits as lines", () => {
    const out = form({ open: { size: true } });
    expect(out).toMatch(/<select class="ctl" aria-label="size by"><option value="contracts" selected="">Contracts<\/option><option value="max_risk">Risk budget<\/option>/);
    expect(out).toContain('<input id="s-contracts" class="ctl" style="width:72px" type="number" inputMode="numeric" step="1" min="1" aria-label="contracts" value="1"/>');
    expect(out).toMatch(/<span class="word">at most<\/span><input id="s-max_open"[^>]*value="3"\/><span class="word">open at once<\/span>/);
    expect(out).toContain("+ <!-- -->Step after a run".replace("<!-- -->", ""));
  });

  it("locks every section of a saved automation and offers no next step", () => {
    const out = form({ readOnly: true, open: { trade: true } });
    expect(out.match(/<fieldset class="builder-lock" disabled="">/g)?.length).toBe(5);
    expect(out).not.toContain("Next: ");
    expect(out).not.toContain("is-cur");
  });

  it("says so, rather than drawing fields, for a module that sends no words", () => {
    expect(form({ schema: { ...SCHEMA, words: undefined } })).toContain("sends no words for the form");
  });

  it("puts a ? on each section, opening its own help", () => {
    const out = form({});
    for (const t of ["1 · Trade", "2 · Entry", "3 · Filters", "4 · Exits", "5 · Size and limits"]) expect(out).toContain(`aria-label="About ${t}"`);
  });
});

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

describe("editing the legs", () => {
  const old = {
    ...IC,
    legs: [
      { name: "short_put", right: "P", side: -1, strike: { rule: "delta", target: 0.35 } },
      { name: "long_put", right: "P", side: 1, strike: { rule: "from_leg", leg: "short_put", points: -20 } },
    ],
  };
  const out = html(<LegFields schema={SCHEMA} spec={old} onChange={() => {}} />);

  it("shows an older leg by its action and side in the page's words", () => {
    expect(out).toMatch(/id="f-leg-0-action"[^]*?value="sell" selected="">Sell to open</);
    expect(out).toMatch(/id="f-leg-1-action"[^]*?value="buy" selected="">Buy to open</);
    expect(out).toMatch(/id="f-leg-0-right"[^]*?value="put" selected="">Put</);
  });

  it("offers only the legs placed before this one for a leg reference, each in words", () => {
    expect(out).toContain('<select id="f-leg-1-leg" class="builder-input" required="" aria-required="true"><option value="">choose…</option><option value="short_put" selected="">Short Put</option></select>');
  });

  it("offers the engine's default match when none is set", () => {
    expect(out).toContain('<option value="" selected="">default (nearest)</option>');
    expect(out).toContain('<option value="" selected="">default (exact)</option>');
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

describe("the section help", () => {
  it("has a title and sections for every part of the page", () => {
    for (const key of ["underlying", "signal", "gates", "structure", "exits", "today", "runs"]) {
      const h = SECTION_HELP[key]!;
      expect(h.title.length).toBeGreaterThan(5);
      expect(html(<>{h.body}</>)).toContain("<h4>");
    }
    expect(html(<>{SECTION_HELP["structure"]!.body}</>)).toContain("A sold leg needs a bid");
  });
});
