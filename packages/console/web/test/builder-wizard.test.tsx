import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BuilderSchema } from "@console/shared";
import { BuilderWizard, Coverage, STEPS, TIPS, holdsCloseSnapshot, plainBlocks, problemKey, problemsUpTo, type StepId } from "../src/pages/Builder/BuilderWizard";
import { SuggestionLine } from "../src/pages/Builder/BuilderPage";

const html = (el: React.ReactElement) => renderToString(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>).replace(/<!--\s*-->/g, "");

const CREDIT = { profit_target_pct: 50, stop_credit_multiple: 1, close_at_dte: 21 };
const PCS = {
  symbol: "SPY",
  expiry: { kind: "between", min: 45, max: 60 },
  entry: { kind: "time", at: "15:55", window: 5 },
  weekdays: [0, 1, 2, 3, 4],
  legs: [
    { name: "short_put", right: "P", side: -1, strike: { rule: "delta", target: 0.2 } },
    { name: "long_put", right: "P", side: 1, strike: { rule: "delta", target: 0.1 } },
  ],
  exit: CREDIT,
  size: { contracts: 1 },
  limits: { max_open: 1, per_day: 1 },
};

const SCHEMA: BuilderSchema = {
  blocks: { structure: ["symbol", "expiry", "legs"], entry: ["entry", "weekdays", "skip_events"], gates: ["gates"], exit: ["exit"], size: ["size", "limits"] },
  entry: { time: { at: true, window: false }, orb: { minutes: true, direction: true, latest: true, single_shot: false } },
  orb_directions: ["above", "below"],
  trigger_forms: {},
  signals: {},
  signal_rules: [],
  weekdays: [0, 1, 2, 3, 4],
  event_labels: ["FOMC", "NFP"],
  expiry: { between: { min: true, max: true, prefer: false }, dte: { days: true, tolerance: false } },
  strike_rules: { delta: { keys: { target: true }, matches: ["nearest"], default_match: "nearest" } },
  gates: [
    { gate: "vix_level", stage: "signal", runs: "market", params: [{ name: "min", required: false, default: null }] },
    { gate: "mid_price", stage: "structure", runs: "structure", params: [{ name: "min", required: false, default: null }] },
  ],
  exits: ["profit_target_pct", "stop_credit_multiple", "close_at_dte"],
  limits: ["max_open", "per_day"],
  sizes: { contracts: ["contracts"] },
  progression: [],
  shapes: {
    short_put: { label: "Short put", about: "Sell one put below the market.", risk: "undefined risk: cash-secured", credit: true, spec: { ...PCS, legs: [PCS.legs[0]] } },
    put_credit_spread: { label: "Put credit spread", about: "Sell a put, buy a lower one.", risk: "defined risk", credit: true, spec: PCS },
    custom: { label: "Custom legs", about: "Start from one leg.", risk: "you set the risk", credit: false, spec: { ...PCS, exit: { close_at_dte: 21 } } },
  },
  credit_rules: CREDIT,
};

const at = (openAt: StepId) =>
  html(<BuilderWizard schema={SCHEMA} automations={[]} openAt={openAt} onCancel={() => {}} onAnother={() => {}} onOpenForm={() => {}} onSaved={() => {}} />);

describe("which step a module problem belongs to", () => {
  it("reads the key from each form the module writes", () => {
    expect(problemKey("my-pcs", "my-pcs.legs[0].strike: unknown rule 'x'")).toBe("legs");
    expect(problemKey("my-pcs", "my-pcs.exit.profit_target_pct: must be a number")).toBe("exit");
    expect(problemKey("my-pcs", "my-pcs: missing 'limits'")).toBe("limits");
    expect(problemKey("my-pcs", "my-pcs: unknown key 'colour'")).toBe("colour");
    expect(problemKey("my-pcs", "name: lower case letters, digits and hyphens")).toBe("name");
    expect(problemKey("my-pcs", "something else entirely")).toBeNull();
  });

  it("stops a step only on its own and earlier steps' problems", () => {
    const ps = ["my-pcs.symbol: unknown", "my-pcs.exit.close_at_dte: must be a number", "my-pcs: missing 'limits'"];
    expect(problemsUpTo("start", "my-pcs", ps)).toEqual([]);
    expect(problemsUpTo("underlying", "my-pcs", ps)).toEqual(["my-pcs.symbol: unknown"]);
    expect(problemsUpTo("gates", "my-pcs", ps)).toEqual(["my-pcs.symbol: unknown"]);
    expect(problemsUpTo("exits", "my-pcs", ps)).toEqual(ps);
  });
});

describe("the entry's cadence", () => {
  it("is the engine's own window test against the 15:59 snapshot", () => {
    expect(holdsCloseSnapshot({ kind: "time", at: "15:55", window: 5 })).toBe(true);
    expect(holdsCloseSnapshot({ kind: "time", at: "15:55" })).toBe(true);
    expect(holdsCloseSnapshot({ kind: "time", at: "15:54", window: 5 })).toBe(false);
    expect(holdsCloseSnapshot({ kind: "time", at: "15:59", window: 1 })).toBe(true);
    expect(holdsCloseSnapshot({ kind: "time", at: "10:00", window: 5 })).toBe(false);
    expect(holdsCloseSnapshot({ kind: "orb", minutes: 30 })).toBe(true);
  });

  it("does not warn on the entry step for the shapes' 15:55 entry", () => {
    expect(at("entry")).not.toContain("never meets the 15:59 daily snapshot");
  });
});

describe("the review, in plain English", () => {
  it("reads each block from the spec", () => {
    const b = plainBlocks(PCS);
    expect(b.structure).toBe("SPY. The listed expiry 45-60 days out, the nearest first. Sell a Short Put at 0.2 delta, Buy a Long Put at 0.1 delta.");
    expect(b.entry).toBe("Enter at 15:55, for 5 minutes. Monday to Friday.");
    expect(b.gates).toBe("None: every eligible day is entered.");
    expect(b.exit).toBe("Take 50% of the most it can make; stop at a loss of 1x the credit; close at 21 days to expiry.");
    expect(b.size).toBe("1 contract; at most 1 open and 1 a day.");
  });
});

describe("the wizard", () => {
  it("opens on the start step with the schema's shapes and the spread chosen", () => {
    const out = at("start");
    expect(out).toContain("What shape will it trade?");
    for (const s of Object.values(SCHEMA.shapes)) expect(out).toContain(s.label);
    expect(out).toMatch(/role="radio" aria-checked="true" class="wiz-shape is-on"><span class="wiz-shape-t">Put credit spread/);
    expect(out).toContain("Next: Underlying and expiry");
    expect(out).toContain("Open in the full form");
  });

  it("marks reached steps done, the current one current, and later ones disabled", () => {
    const out = at("exits");
    expect(out.match(/class="wiz-st is-done"/g)).toHaveLength(5);
    expect(out).toMatch(/class="wiz-st is-current"[^>]*aria-current="step"/);
    expect(out).toMatch(/class="wiz-st" disabled=""/);
  });

  it("offers Skip gates only on the gates step and Save (off) only on the review", () => {
    expect(at("gates")).toContain("Skip gates");
    expect(at("legs")).not.toContain("Skip gates");
    const review = at("review");
    expect(review).toContain("Save (off)");
    expect(review).not.toContain("Next:");
    expect(review.match(/>Edit</g)).toHaveLength(5);
    expect(at("exits")).not.toContain("Save (off)");
  });

  it("starts a credit shape's exits from the credit-trade rules", () => {
    const out = at("exits");
    expect(out).toContain("Your credit-trade rules");
    expect(out).toContain("Take 50% of the most it can make");
  });

  it("puts a ? beside every part a newcomer would ask about, closed until clicked", () => {
    const seen = new Set<string>();
    for (const s of STEPS) {
      const out = at(s.id);
      expect(out, s.id).toContain('class="wiz-q"');
      expect(out, s.id).not.toContain('role="note"');
      for (const m of out.matchAll(/class="wiz-q" aria-label="([^"]+)"/g)) seen.add(m[1]!);
    }
    // off and run sit on the saved screen, reached only by a save
    expect(seen.size).toBe(Object.keys(TIPS).length - 2);
  });

  it("never promises a stop the engine does not have", () => {
    for (const [k, t] of Object.entries(TIPS)) expect(t, k).not.toMatch(/50% of (the )?max loss|guaranteed/i);
    expect(TIPS["rules"]).toContain("no fallback stop");
  });
});

describe("the coverage", () => {
  const SHORT = {
    blocks: ["structure"],
    window: ["2025-11-14", "2026-11-14"] as [string, string],
    possible: false,
    licensed: false,
    needs: [
      {
        field: "chain",
        expiry: "all",
        cadence: "1d",
        covered: false,
        source: null,
        licensed: false,
        why: "none of our stores holds all expiries at 1d",
        monthly: { source: "dolt-chains", first: "2019-02-09", through: "2026-11-13" },
      },
    ],
  };

  it("says why no store is a candidate and offers monthlies where they would be covered", () => {
    const out = html(<Coverage s={SHORT} onMonthly={() => {}} />);
    expect(out).toContain("chain (all) at 1d: none of our stores holds all expiries at 1d");
    expect(out).toContain("Monthlies only would be covered by dolt-chains from 2019-02-09.");
    expect(out).toContain(">Use monthlies only<");
    expect(html(<Coverage s={SHORT} />)).not.toContain("Use monthlies only");
    const form = html(<SuggestionLine s={SHORT} />);
    expect(form).toContain("chain: none of our stores holds all expiries at 1d");
    expect(form).toContain('With the expiry rule&#x27;s &quot;monthly&quot; set, dolt-chains would cover it from 2019-02-09.');
  });

  it("names the window a covered run takes, and a licensed store as licensed", () => {
    const ok = { ...SHORT, possible: true, window: ["2025-11-14", "2026-11-13"] as [string, string], needs: [{ ...SHORT.needs[0]!, covered: true, source: "dolt-chains", through: "2026-11-13" }] };
    expect(html(<Coverage s={ok} />)).toContain("Covered by our own data, 2025-11-14 to 2026-11-13");
    expect(html(<Coverage s={{ ...ok, licensed: true }} />)).toContain("Covered by the licensed vendor store");
  });

  it("reads a monthly rule as monthlies", () => {
    expect(plainBlocks({ ...PCS, expiry: { kind: "between", min: 45, max: 60, monthly: true } }).structure).toContain("The listed monthly expiry 45-60 days out");
  });
});
