import type { SlideDef } from "./types";
import { MODULE_ORDER, type ModuleId } from "./moduleOrder";

/**
 * Pure stepping helpers for the lightbox carousel -- unit-testable without a DOM. Within a
 * module, stepping skips slides marked `available: false` (a later-phase placeholder should never
 * be where "next slide" lands). At a module's boundary, stepping crosses to the next/previous
 * module's OWN default (first) slide -- a deliberate simplification: the alternative (landing on
 * the previous module's LAST slide when stepping backward across a boundary) would need a global
 * map of every module's slide list just to support one edge case, and "a fresh module always
 * opens on its first slide" is the same rule `/module` (no slide segment) already uses.
 */

function availableIndices(slides: SlideDef[]): number[] {
  const idx = slides.map((s, i) => (s.available === false ? -1 : i)).filter((i) => i >= 0);
  return idx.length > 0 ? idx : slides.map((_, i) => i);
}

/** The next available slide id within this module, or null at the end (cross to the next module). */
export function nextSlideId(slides: SlideDef[], currentId: string): string | null {
  const avail = availableIndices(slides);
  const pos = avail.findIndex((i) => slides[i]?.id === currentId);
  const at = pos === -1 ? 0 : pos;
  return at + 1 < avail.length ? (slides[avail[at + 1]!]?.id ?? null) : null;
}

/** The previous available slide id within this module, or null at the start. */
export function prevSlideId(slides: SlideDef[], currentId: string): string | null {
  const avail = availableIndices(slides);
  const pos = avail.findIndex((i) => slides[i]?.id === currentId);
  const at = pos === -1 ? 0 : pos;
  return at - 1 >= 0 ? (slides[avail[at - 1]!]?.id ?? null) : null;
}

export function firstSlideId(slides: SlideDef[]): string | undefined {
  const avail = availableIndices(slides);
  return slides[avail[0] ?? 0]?.id;
}

export function lastSlideId(slides: SlideDef[]): string | undefined {
  const avail = availableIndices(slides);
  return slides[avail[avail.length - 1] ?? 0]?.id;
}

export function nextModule(current: ModuleId): ModuleId {
  const i = MODULE_ORDER.indexOf(current);
  return MODULE_ORDER[(i + 1) % MODULE_ORDER.length]!;
}

export function prevModule(current: ModuleId): ModuleId {
  const i = MODULE_ORDER.indexOf(current);
  return MODULE_ORDER[(i - 1 + MODULE_ORDER.length) % MODULE_ORDER.length]!;
}
