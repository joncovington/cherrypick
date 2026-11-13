import { useSyncExternalStore } from "react";
import type { ConfigTargetId } from "@console/shared";

/**
 * Staged config edits, held outside React so a draft survives navigating away from the page and
 * back. Leaving a half-finished edit and losing it silently is the worse failure here: nothing on
 * screen would say the change was dropped. The nav shows a dot while anything is staged, and the
 * browser warns on close.
 *
 * A staged value is only "dirty" relative to what the file currently says — see `isDirty` — so a
 * refetch that happens to match a staged value clears it rather than leaving a phantom change.
 */

export interface StagedKey {
  target: ConfigTargetId;
  pointer: string;
}

const SEP = "␟";
const staged = new Map<string, unknown>();
const listeners = new Set<() => void>();

function keyOf(target: ConfigTargetId, pointer: string): string {
  return `${target}${SEP}${pointer}`;
}

function emit(): void {
  snapshotVersion += 1;
  for (const l of listeners) l();
}

let snapshotVersion = 0;

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function stageEdit(target: ConfigTargetId, pointer: string, value: unknown): void {
  staged.set(keyOf(target, pointer), value);
  emit();
}

export function unstageEdit(target: ConfigTargetId, pointer: string): void {
  if (staged.delete(keyOf(target, pointer))) emit();
}

export function getStaged(target: ConfigTargetId, pointer: string): { has: boolean; value: unknown } {
  const k = keyOf(target, pointer);
  return { has: staged.has(k), value: staged.get(k) };
}

/** Every staged edit, as the save route wants them: grouped by target. */
export function stagedByTarget(): Map<ConfigTargetId, Array<{ pointer: string; value: unknown }>> {
  const out = new Map<ConfigTargetId, Array<{ pointer: string; value: unknown }>>();
  for (const [k, value] of staged) {
    const [target, pointer] = k.split(SEP) as [ConfigTargetId, string];
    const list = out.get(target) ?? [];
    list.push({ pointer, value });
    out.set(target, list);
  }
  return out;
}

export function clearStaged(keys: StagedKey[]): void {
  let changed = false;
  for (const { target, pointer } of keys) changed = staged.delete(keyOf(target, pointer)) || changed;
  if (changed) emit();
}

export function clearAllStaged(): void {
  if (staged.size === 0) return;
  staged.clear();
  emit();
}

/** Re-render on any staged change. The version number is the snapshot — the Map is mutable. */
export function useStagedVersion(): number {
  // The same snapshot on the server: a server render (the tests') reads the staged edits as they are.
  return useSyncExternalStore(subscribe, () => snapshotVersion, () => snapshotVersion);
}

export function useDirtyCount(): number {
  useStagedVersion();
  return staged.size;
}

// A staged edit is only in the browser; closing the tab drops it. In-app navigation deliberately
// does not warn — the draft survives that.
if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", (e) => {
    if (staged.size > 0) e.preventDefault();
  });
}

/** Deep equality good enough for config scalars, string lists, window pairs and the per-name
 *  override objects (`positions`), whose key order does not matter. */
export function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => sameValue(v, b[i]));
  }
  if (a !== null && b !== null && typeof a === "object" && typeof b === "object" && !Array.isArray(a) && !Array.isArray(b)) {
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    return (
      ka.length === kb.length &&
      ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && sameValue((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]))
    );
  }
  return false;
}
