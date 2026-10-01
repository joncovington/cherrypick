import { describe, it, expect } from "vitest";
import { isChunkLoadError, reloadForStaleChunk, PageErrorBoundary } from "../src/components/shell/PageErrorBoundary";

/**
 * A page whose code was deleted by a rebuild went blank until a manual refresh. The boundary
 * reloads once for that failure and only that failure, and the guard keeps a build that really
 * lacks a chunk from reloading forever.
 */

function memoryStorage() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
}

describe("stale chunk handling", () => {
  it("recognises a failed dynamic import in each browser's words, and nothing else", () => {
    expect(isChunkLoadError(new TypeError("Failed to fetch dynamically imported module: http://x/assets/MeicLightbox-abc.js"))).toBe(true);
    expect(isChunkLoadError(new TypeError("error loading dynamically imported module"))).toBe(true);
    expect(isChunkLoadError(new TypeError("Importing a module script failed."))).toBe(true);
    expect(isChunkLoadError(new TypeError("Cannot read properties of undefined (reading 'map')"))).toBe(false);
  });

  it("reloads once, then not again within the guard window", () => {
    const storage = memoryStorage();
    let reloads = 0;
    const reload = () => void reloads++;
    expect(reloadForStaleChunk(1_000_000, storage, reload)).toBe(true);
    expect(reloadForStaleChunk(1_005_000, storage, reload)).toBe(false);
    expect(reloads).toBe(1);
    expect(reloadForStaleChunk(1_020_000, storage, reload)).toBe(true);
    expect(reloads).toBe(2);
  });

  it("an error becomes the boundary's state, so the page shows it instead of unmounting", () => {
    const err = new Error("boom");
    expect(PageErrorBoundary.getDerivedStateFromError(err)).toEqual({ error: err });
  });
});
