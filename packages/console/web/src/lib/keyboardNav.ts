import type { SuiteFeatures } from "@console/shared";
import { visibleNavLinks } from "../components/shell/navLinks";

/**
 * The keyboard shortcuts the command palette advertises.
 *
 * The hints used to hang off the header's hamburger menu, which itself is gone (2026-10-11, the
 * rail became the one nav on every page): the palette is where a reader now sees what `g o`,
 * `1`–`8` and `j`/`k` do, and it is also where they are SEARCHABLE rather than a fixed list.
 *
 * Pure on purpose. The decision — is this key an action, and which — is the part worth testing,
 * and it is testable as a function in a package with no DOM. The hook around it does nothing but
 * listen, ask, and navigate (or open the palette).
 *
 * `j`/`k` step tabs within the current module and stop at the ends. No wrapping into the next
 * module: that was the carousel's behaviour and it is the thing the rail replaced. It works only
 * for modules on the frame, because only they have a tab list that can be known without loading
 * the module.
 */
export const CHORD_MS = 1000;

export interface KeyState {
  /** When `g` was pressed, or null. */
  pendingG: number | null;
}

export type KeyAction =
  | { kind: "navigate"; to: string }
  | { kind: "slide"; id: string }
  | { kind: "palette" }
  | null;

/** What kind of palette-open key this raw event is, if it is one: `"chord"` for Ctrl/Cmd+K —
 *  the palette's own chord, which works from inside a text field, where every other key belongs
 *  to the field — or `"question"` for a bare `?`, which IS a typed character and so only opens
 *  when focus is not in one. One helper, because the hook and the tests must not re-derive it. */
export function paletteKeyFor(e: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean }): "chord" | "question" | null {
  if (e.altKey) return null;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") return "chord";
  if (e.key === "?" && !e.ctrlKey && !e.metaKey) return "question";
  return null;
}

export interface KeyEvent {
  key: string;
  now: number;
  /** Focus is in a text field — every key belongs to it, including `g` and the digits. */
  editable: boolean;
  modified: boolean;
  slides: string[];
  slide: string | null;
  /** What the suite has turned on: the digits name the VISIBLE modules, in the palette's order,
   *  and a chord to a hidden page does nothing. Undefined (loading, failed) means everything. */
  features?: SuiteFeatures;
  /** Pre-computed by `paletteKeyFor`: which palette-open key this is, if any. Checked before the
   *  editable/modified short-circuits, since Ctrl+K must work from a text field. */
  paletteKey?: "chord" | "question" | null;
}

export function reduceKey(state: KeyState, ev: KeyEvent): { state: KeyState; action: KeyAction } {
  const idle: KeyState = { pendingG: null };

  // The palette opens first: the chord (Ctrl/Cmd+K) even from inside a text field, the bare `?`
  // only when focus is not in one — a question mark typed into a search box is a question mark.
  if (ev.paletteKey === "chord") return { state: idle, action: { kind: "palette" } };
  if (ev.paletteKey === "question" && !ev.editable) return { state: idle, action: { kind: "palette" } };

  // Typing a symbol into a search box must not navigate, and neither should Ctrl+R.
  if (ev.editable || ev.modified) return { state: idle, action: null };

  // Inside the chord window, this key completes `g …`. Outside it, a stale `g` from a minute ago
  // must not turn an `o` into a navigation.
  const links = visibleNavLinks(ev.features);
  if (state.pendingG !== null && ev.now - state.pendingG <= CHORD_MS) {
    const hit = links.suite.find((l) => l.key === ev.key);
    return { state: idle, action: hit === undefined ? null : { kind: "navigate", to: hit.to } };
  }

  if (ev.key === "g") return { state: { pendingG: ev.now }, action: null };

  if (/^[1-9]$/.test(ev.key)) {
    const hit = links.modules[Number(ev.key) - 1];
    return { state: idle, action: hit === undefined ? null : { kind: "navigate", to: hit.to } };
  }

  if (ev.key === "j" || ev.key === "k") {
    if (ev.slides.length === 0 || ev.slide === null) return { state: idle, action: null };
    const i = ev.slides.indexOf(ev.slide);
    if (i === -1) return { state: idle, action: null };
    const next = ev.key === "j" ? i + 1 : i - 1;
    // Stop at the ends. Wrapping past the last tab is what the carousel did, and it is what the
    // rail replaced.
    if (next < 0 || next >= ev.slides.length) return { state: idle, action: null };
    return { state: idle, action: { kind: "slide", id: ev.slides[next]! } };
  }

  return { state: idle, action: null };
}
