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
  it("g then o goes to the overview", () => {
    const first = press(IDLE, "g");
    expect(first.action).toBeNull();
    expect(first.state.pendingG).toBe(1000);
    expect(press(first.state, "o", { now: 1200 }).action).toEqual({ kind: "navigate", to: "/" });
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
    expect(press(IDLE, "1").action).toEqual({ kind: "navigate", to: "/meic" });
    expect(press(IDLE, "2").action).toEqual({ kind: "navigate", to: "/flies" });
    expect(press(IDLE, "8").action).toEqual({ kind: "navigate", to: "/gex" });
  });

  it("ignores a digit no module claims", () => {
    expect(press(IDLE, "9").action).toBeNull();
    expect(press(IDLE, "0").action).toBeNull();
  });

  it("name the VISIBLE modules: an off module's digit goes to the next one shown", () => {
    // pmcc off: 3 is curve now, 7 is gex, and 8 (gex's old digit) is nobody's.
    const features = {
      ok: true as const,
      capabilities: {},
      modules: { pmcc: { configured: false, enabled: false, missing: [] } },
      services: {},
      features: {},
    };
    expect(press(IDLE, "3", { features }).action).toEqual({ kind: "navigate", to: "/curve" });
    expect(press(IDLE, "7", { features }).action).toEqual({ kind: "navigate", to: "/gex" });
    expect(press(IDLE, "8", { features }).action).toBeNull();
  });

  it("a chord to a hidden page does nothing", () => {
    const features = { ok: true as const, capabilities: {}, modules: {}, services: {}, features: { advisor: false } };
    const first = press(IDLE, "g");
    expect(press(first.state, "a", { now: 1100, features }).action).toBeNull();
    expect(press(first.state, "a", { now: 1100 }).action).toEqual({ kind: "navigate", to: "/advisor" });
  });

  it("show every module when the bridge failed", () => {
    expect(press(IDLE, "3", { features: { ok: false, error: "bridge down" } }).action).toEqual({ kind: "navigate", to: "/pmcc" });
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
