import { useSyncExternalStore } from "react";

/**
 * The console's toast queue -- a module-level singleton store, same shape as `prefs.ts`'s own
 * external store (a version-bumping listener set `useSyncExternalStore` subscribes to), so a toast
 * can be pushed from anywhere (an action's onSuccess, a background poll) without threading a
 * dispatcher through props.
 */

export type ToastTone = "entry" | "exit" | "warn" | "info";

export interface Toast {
  id: number;
  tone: ToastTone;
  title: string;
  message: string;
  ttlMs: number;
}

let toasts: Toast[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const timers = new Map<number, number>();

function notify(): void {
  for (const l of listeners) l();
}

export function dismissToast(id: number): void {
  const t = timers.get(id);
  if (t !== undefined) {
    window.clearTimeout(t);
    timers.delete(id);
  }
  toasts = toasts.filter((x) => x.id !== id);
  notify();
}

/** Default 6s -- long enough to actually read a P&L line, matching what a trade event carries. */
export function pushToast(t: { tone: ToastTone; title: string; message?: string; ttlMs?: number }): void {
  const toast: Toast = { id: nextId++, message: "", ttlMs: 6000, ...t };
  toasts = [...toasts, toast];
  notify();
  // Auto-dismiss regardless of prefers-reduced-motion -- that setting turns off the slide/fade
  // animation (styles.css), not the toast's own lifetime; a reduced-motion viewer still wants it
  // to go away on its own.
  const timer = window.setTimeout(() => dismissToast(toast.id), toast.ttlMs);
  timers.set(toast.id, timer);
}

const EMPTY: Toast[] = [];

export function useToasts(): Toast[] {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => toasts,
    // Server snapshot: no toast fires until a client-side poll or action runs, so an empty list
    // is the only correct answer under SSR (renderToString has no getServerSnapshot by default,
    // which React treats as a hard error rather than a warning).
    () => EMPTY,
  );
}
