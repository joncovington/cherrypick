import { useSyncExternalStore } from "react";

/**
 * Whether the command palette is open — a two-value store, because three components must agree on
 * it without prop-drilling through the shell: the keyboard hook opens it, the top bar's button
 * opens it, and the palette itself closes it (Escape, a navigation, a pick). An event-bus or a
 * context would be heavier than the one fact being shared.
 */

let open = false;
const listeners = new Set<() => void>();

const subscribe = (l: () => void): (() => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

/** The server snapshot is the same version number; the palette never renders on the server. */
const snapshot = (): boolean => open;

export function usePaletteOpen(): boolean {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

export function setPaletteOpen(next: boolean): void {
  if (open === next) return;
  open = next;
  for (const l of listeners) l();
}

export function togglePalette(): void {
  setPaletteOpen(!open);
}
