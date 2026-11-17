import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BuilderAutomation, BuilderSchema } from "@console/shared";
import { ActionRow, NEW_SPEC, runBlocked, SuggestionLine, Workspace } from "../src/pages/Builder/BuilderPage";

const html = (el: React.ReactElement) =>
  renderToString(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>).replace(/<!--\s*-->/g, "");

const BASE: BuilderAutomation = {
  name: "mine",
  group: "ensemble",
  symbol: "SPX",
  enabled: false,
  note: null,
  start: null,
  register: { item: 75, verdict: "no_verdict" },
  summary: "structure backtested (item 75, no_verdict); exit changed since its backtest",
  blocks: [
    { block: "structure", state: "backtested", keys: { symbol: "SPX" } },
    { block: "exit", state: "changed", keys: { exit: { close_at_dte: 1 } } },
    { block: "size", state: "not_backtested", keys: {} },
  ],
  forward: null,
  suggestion: null,
};

const SCHEMA: BuilderSchema = {
  blocks: {},
  entry: { time: { at: true, window: false } },
  orb_directions: ["above", "below"],
  trigger_forms: {},
  signals: {},
  signal_rules: [],
  weekdays: [0, 1, 2, 3, 4],
  event_labels: [],
  expiry: { dte: { days: true } },
  strike_rules: {},
  gates: [],
  exits: [],
  limits: ["max_open", "per_day"],
  sizes: { contracts: ["contracts"] },
  progression: [],
};

const POSSIBLE = { blocks: ["exit"], window: ["2025-11-13", "2026-11-13"] as [string, string], needs: [], possible: true, licensed: false };

describe("the workspace", () => {
  it("names a saved automation's switch, group, start and register label, and offers JSON", () => {
    const out = html(<Workspace a={BASE} schema={SCHEMA} mode={{ kind: "saved", name: "mine" }} onSaved={() => {}} />);
    expect(out).toContain('value="mine"');
    expect(out).toContain('class="chip">off</span>');
    expect(out).toContain("group ensemble");
    expect(out).toContain("start not set");
    expect(out).toContain("item 75: no verdict");
    expect(out).toContain(">Edit as JSON</button>");
    expect(out).toContain("Today, live");
    expect(out).toContain("Explore runs · this automation");
    expect(out).toContain('aria-label="About Today, live"');
    expect(out).toContain('aria-label="About Explore runs"');
  });

  it("starts a new automation from the keys the loop requires, and has no runs yet", () => {
    const out = html(<Workspace a={null} schema={SCHEMA} mode={{ kind: "new" }} onSaved={() => {}} />);
    expect(out).toContain("1 · Underlying and data");
    expect(out).toContain('class="chip">not saved</span>');
    expect(out).not.toContain("Explore runs · this automation");
    expect(Object.keys(NEW_SPEC).sort()).toEqual(["entry", "expiry", "legs", "limits", "size", "symbol", "weekdays"]);
  });

  it("offers a copy under a new name", () => {
    const out = html(<Workspace a={BASE} schema={SCHEMA} mode={{ kind: "copy", from: "mine" }} onSaved={() => {}} />);
    expect(out).toContain('value="mine-copy"');
    expect(out).not.toMatch(/<input id="builder-name"[^>]*disabled/);
  });
});

describe("the action row", () => {
  it("checks and saves off; declaring and switching on are never buttons that work", () => {
    const out = html(<ActionRow busy={false} canCheck canRun={false} runWhy="save the automation first" onCheck={() => {}} onSave={() => {}} onRun={() => {}} />);
    expect(out).toContain(">Check</button>");
    expect(out).toContain(">Save (off)</button>");
    expect(out).toMatch(/<button[^>]*disabled[^>]*>Explore run<\/button>/);
    expect(out).toMatch(/<button[^>]*disabled[^>]*>Declare…<\/button>/);
    expect(out).toMatch(/<button[^>]*disabled[^>]*>Promote to paper<\/button>/);
    expect(out).toContain("Explore run: save the automation first");
  });

  it("offers Explore run only for a saved, unedited automation our data covers", () => {
    const saved = { kind: "saved", name: "mine" } as const;
    expect(runBlocked({ ...BASE, suggestion: POSSIBLE }, false, saved)).toBeNull();
    expect(runBlocked({ ...BASE, suggestion: POSSIBLE }, true, saved)).toContain("save the edit first");
    expect(runBlocked({ ...BASE, suggestion: { ...POSSIBLE, possible: false } }, false, saved)).toContain("does not cover");
    expect(runBlocked(BASE, false, saved)).toBe("every block is backtested already");
    expect(runBlocked(null, false, { kind: "new" })).toBe("save the automation first");
  });
});

describe("the coverage line", () => {
  it("says whether our own data covers a backtest, and the nearest store when not", () => {
    const yes = html(<SuggestionLine s={POSSIBLE} />);
    expect(yes).toContain('data-possible="yes"');
    expect(yes).toContain("Our own data covers a backtest of exit from 2025-11-13 to 2026-11-13.");
    const no = html(
      <SuggestionLine
        s={{
          blocks: ["exit"],
          window: ["2025-11-13", "2026-11-13"],
          possible: false,
          licensed: false,
          needs: [
            { field: "chain", expiry: "0dte", cadence: "1m", covered: false, source: null, licensed: false,
              nearest: { source: "recorded-0dte", first: "2026-10-13", last: "2026-11-13", why: "starts 2026-10-13" } },
            { field: "spot", cadence: "1m", covered: true, source: "spot-trail", licensed: false },
          ],
        }}
      />,
    );
    expect(no).toContain('data-possible="no"');
    expect(no).toContain("chain (0dte) at 1m");
    expect(no).not.toContain("spot at 1m");
    expect(no).toContain("nearest for chain: recorded-0dte 2026-10-13 to 2026-11-13 (starts 2026-10-13)");
  });

  it("names the licensed store when the vendor's covers what our own data does not", () => {
    const need = (field: string) => ({ field, cadence: "1m", covered: true, source: "vendor-0dte-spx-60s", licensed: true });
    const out = html(<SuggestionLine s={{ ...POSSIBLE, licensed: true, needs: [need("chain"), need("greeks")] }} />);
    expect(out).toContain('data-licensed="yes"');
    expect(out).toContain("The licensed vendor store (vendor-0dte-spx-60s) covers a backtest of exit");
    expect(out).toContain("Its result is licensed");
    expect(out).not.toContain("Our own data covers");
  });
});
