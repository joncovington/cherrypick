import { useEffect, useSyncExternalStore } from "react";
import { useLocation } from "react-router-dom";
import { useBoolPref, writePref } from "../../lib/prefs";

/** The rail's open/closed state on a wide window: a console preference, so it follows the reader
 *  to the desktop shell. Absent means collapsed (2026-10-01). */
export const RAIL_PREF = "navExpanded";

/** Below this the rail's column is the icon strip and an open rail overlays the content. */
export const NARROW_QUERY = "(max-width: 1000px)";

function subscribeMedia(cb: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {};
  const mq = window.matchMedia(NARROW_QUERY);
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
}
const narrowNow = () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(NARROW_QUERY).matches;

/* On a narrow window (a phone, a tablet, a desktop at high scaling) opening the rail is a
   moment, not a setting: it overlays the page, closes on the next navigation, and never touches
   the saved wide-window preference. */
let overlayOpen = false;
const listeners = new Set<() => void>();
function setOverlay(open: boolean): void {
  if (overlayOpen === open) return;
  overlayOpen = open;
  for (const l of listeners) l();
}
function subscribeOverlay(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/** [expanded, toggle]: the overlay state on a narrow window, the saved pref on a wide one. */
export function useRailExpanded(): [boolean, () => void] {
  const pref = useBoolPref(RAIL_PREF);
  const narrow = useSyncExternalStore(subscribeMedia, narrowNow, () => false);
  const open = useSyncExternalStore(subscribeOverlay, () => overlayOpen, () => false);
  if (narrow) return [open, () => setOverlay(!open)];
  return [pref, () => void writePref(RAIL_PREF, !pref)];
}

/** Close the narrow-window overlay whenever the route changes (a pick from the rail). */
export function useCloseRailOnNavigate(): void {
  const { pathname } = useLocation();
  useEffect(() => setOverlay(false), [pathname]);
}
