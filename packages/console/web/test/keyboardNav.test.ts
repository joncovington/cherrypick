import { describe, it, expect } from "vitest";
import { reduceKey, CHORD_MS, type KeyState } from "../src/lib/keyboardNav";

const IDLE: KeyState = { pendingG: null };
const FLIES = ["session", "forest", "timeline", "openingrange"];

function press(
  state: KeyState,
  key: string,
  over: Partial<Parameters<typeof reduceKey>[1]> = {},
) {
  return reduceKey(state, {
    key,
    now: 1000,
    editable: false,
    modified: false,
    slides: FLIES,
    slide: "forest",
    ...over,
  });
}

describe("the g chord", () => {
  it("g then l goes to the live page", () => {
    const first = press(IDLE, "g");
    expect(first.action).toBeNull();
    expect(first.state.pendingG).toBe(1000);
    expect(press(first.state, "l", { now: 1200 }).action).toEqual({ kind: "navigate", to: "/live" });
  });

  it("forgets a stale g rather than acting on it much later", () => {
    const first = press(IDLE, "g");
    // Pressing `g` and then wandering off should not make the next `o` navigate.
    expect(press(first.state, "o", { now: 1000 + CHORD_MS + 1 }).action).toBeNull();
  });

  it("a key the chord does not name does nothing, and clears the chord", () => {
    const first = press(IDLE, "g");
    const second = press(first.state, "z", { now: 1100 });
    expect(second.action).toBeNull();
    expect(second.state.pendingG).toBeNull();
  });
});

describe("the module digits", () => {
  it("names the same modules the menu advertises, in the same order", () => {
    // 2026-10-30: the live-capable modules, then the Experiments page that holds the rest.
    expect(press(IDLE, "1").action).toEqual({ kind: "navigate", to: "/meic" });
    expect(press(IDLE, "2").action).toEqual({ kind: "navigate", to: "/bwb" });
    expect(press(IDLE, "3").action).toEqual({ kind: "navigate", to: "/earnings" });
    expect(press(IDLE, "4").action).toEqual({ kind: "navigate", to: "/experiments" });
  });

  it("ignores a digit no module claims", () => {
    expect(press(IDLE, "5").action).toBeNull();
    expect(press(IDLE, "9").action).toBeNull();
    expect(press(IDLE, "0").action).toBeNull();
  });

  it("g o reaches Config, whatever else is off", () => {
    const first = press(IDLE, "g");
    expect(press(first.state, "o", { now: 1100 }).action).toEqual({ kind: "navigate", to: "/config" });
  });

  it("g g reaches GEX, now a suite page among the market reads", () => {
    const first = press(IDLE, "g");
    expect(press(first.state, "g", { now: 1100 }).action).toEqual({ kind: "navigate", to: "/gex" });
  });

  it("keep their keys when a module is off: the off module's digit does nothing, the rest do not move", () => {
    // Fixed since 2026-10-30; before, bwb off made 2 earnings.
    const features = {
      ok: true as const,
      capabilities: {},
      modules: { bwb: { configured: false, enabled: false, missing: [] } },
      services: {},
      features: {},
    };
    expect(press(IDLE, "2", { features }).action).toBeNull();
    expect(press(IDLE, "3", { features }).action).toEqual({ kind: "navigate", to: "/earnings" });
  });

  it("a chord to a hidden page does nothing", () => {
    const features = { ok: true as const, capabilities: {}, modules: {}, services: {}, features: { advisor: false } };
    const first = press(IDLE, "g");
    expect(press(first.state, "a", { now: 1100, features }).action).toBeNull();
    expect(press(first.state, "a", { now: 1100 }).action).toEqual({ kind: "navigate", to: "/advisor" });
  });

  it("g b reaches the backtest only once the suite says it is on (it fails closed)", () => {
    const on = { ok: true as const, capabilities: {}, modules: {}, services: {}, features: { backtest: true } };
    const first = press(IDLE, "g");
    expect(press(first.state, "b", { now: 1100 }).action).toBeNull();
    expect(press(first.state, "b", { now: 1100, features: { ok: false, error: "bridge down" } }).action).toBeNull();
    expect(press(first.state, "b", { now: 1100, features: on }).action).toEqual({ kind: "navigate", to: "/backtest" });
  });

  it("show every module when the bridge failed", () => {
    expect(press(IDLE, "2", { features: { ok: false, error: "bridge down" } }).action).toEqual({ kind: "navigate", to: "/bwb" });
  });
});

describe("j and k step tabs", () => {
  it("move one tab within the module", () => {
    expect(press(IDLE, "j").action).toEqual({ kind: "slide", id: "timeline" });
    expect(press(IDLE, "k").action).toEqual({ kind: "slide", id: "session" });
  });

  it("stop at the ends rather than wrapping into the next module", () => {
    // The carousel wrapped; the rail replaced it, and wrapping out of a module by keyboard is
    // exactly the surprise it removed.
    expect(press(IDLE, "k", { slide: "session" }).action).toBeNull();
    expect(press(IDLE, "j", { slide: "openingrange" }).action).toBeNull();
  });

  it("decline for a module with no declared tabs, rather than guessing", () => {
    expect(press(IDLE, "j", { slides: [], slide: null }).action).toBeNull();
  });
});

describe("what must never steal a keystroke", () => {
  it("typing in a field — a symbol search contains every one of these letters", () => {
    for (const key of ["g", "j", "k", "2"]) {
      expect(press(IDLE, key, { editable: true }).action).toBeNull();
    }
  });

  it("a modified key — Ctrl+R is a reload, not a jump to reports", () => {
    expect(press(IDLE, "r", { modified: true }).action).toBeNull();
    expect(press(IDLE, "1", { modified: true }).action).toBeNull();
  });

  it("and a pending chord does not survive any of them", () => {
    const first = press(IDLE, "g");
    expect(press(first.state, "o", { editable: true }).state.pendingG).toBeNull();
  });
});
