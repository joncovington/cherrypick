import type { SuiteFeatures } from "@console/shared";
import { CONFIG_LINK, visibleNavLinks } from "../components/shell/navLinks";

/**
 * The keyboard shortcuts the rail advertises in its link tooltips (`ModuleNav.shortcutTitle`).
 *
 * The header menu that first printed `g o`, `g r` and `1`–`8` is gone (the rail is the one nav);
 * the hints moved to the rail's titles. A hint for a shortcut that does not exist is worse than no
 * hint, so both are driven from `navLinks.ts`.
 *
 * Pure on purpose. The decision — is this key an action, and which — is the part worth testing,
 * and it is testable as a function in a package with no DOM. The hook around it does nothing but
 * listen, ask, and navigate.
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

export type KeyAction = { kind: "navigate"; to: string } | { kind: "slide"; id: string } | null;

export interface KeyEvent {
  key: string;
  now: number;
  /** Focus is in a text field — every key belongs to it, including `g` and the digits. */
  editable: boolean;
  modified: boolean;
  slides: string[];
  slide: string | null;
  /** What the suite has turned on: the digits name the VISIBLE modules, in the menu's order, and
   *  a chord to a hidden page does nothing. Undefined (loading, failed) means everything. */
  features?: SuiteFeatures;
}

export function reduceKey(state: KeyState, ev: KeyEvent): { state: KeyState; action: KeyAction } {
  const idle: KeyState = { pendingG: null };

  // Typing a symbol into a search box must not navigate, and neither should Ctrl+R.
  if (ev.editable || ev.modified) return { state: idle, action: null };

  // Inside the chord window, this key completes `g …`. Outside it, a stale `g` from a minute ago
  // must not turn an `o` into a navigation.
  const links = visibleNavLinks(ev.features);
  if (state.pendingG !== null && ev.now - state.pendingG <= CHORD_MS) {
    // Config is never filtered, so its `g o` (2026-10-30) holds whatever else is off.
    const hit = [...links.suite, CONFIG_LINK].find((l) => l.key === ev.key);
    return { state: idle, action: hit === undefined ? null : { kind: "navigate", to: hit.to } };
  }

  if (ev.key === "g") return { state: { pendingG: ev.now }, action: null };

  // A digit is the module's own, fixed key (2026-10-30); an off module's key does nothing.
  if (/^[1-9]$/.test(ev.key)) {
    const hit = links.modules.find((l) => l.key === ev.key);
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
