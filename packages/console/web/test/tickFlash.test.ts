import { describe, it, expect } from "vitest";
import { FLASH_MS, STALE_TICK_MS, flashFor } from "../src/lib/useTickFlash";
import type { QuoteState } from "../src/lib/wsClient";

/**
 * When a tick earns a flash. A sleeping tab queues its socket messages and delivers them together on
 * wake; each used to restart the flash, so the chips ran through the whole backlog at speed. Only a
 * fresh, live tick on a visible page flashes, and never while a flash is still showing.
 */

const NOW = 1_800_000_000_000;

function q(over: Partial<QuoteState>): QuoteState {
  return { source: "dxlink", direction: "up", ts: NOW, last: 100, ...over };
}

describe("flashFor", () => {
  it("a fresh live tick on a visible page flashes its direction", () => {
    expect(flashFor({ q: q({}), now: NOW, lastFlashAt: 0, hidden: false })).toBe("up");
    expect(flashFor({ q: q({ direction: "down" }), now: NOW, lastFlashAt: 0, hidden: false })).toBe("down");
  });
  it("a backlog tick from a sleeping tab does not flash", () => {
    expect(flashFor({ q: q({ ts: NOW - STALE_TICK_MS - 1 }), now: NOW, lastFlashAt: 0, hidden: false })).toBeNull();
  });
  it("a burst flashes once: no new flash while one is still showing", () => {
    expect(flashFor({ q: q({}), now: NOW, lastFlashAt: NOW - FLASH_MS + 1, hidden: false })).toBeNull();
    expect(flashFor({ q: q({}), now: NOW, lastFlashAt: NOW - FLASH_MS, hidden: false })).toBe("up");
  });
  it("nothing flashes on a hidden page, for a cached price, or without a direction", () => {
    expect(flashFor({ q: q({}), now: NOW, lastFlashAt: 0, hidden: true })).toBeNull();
    expect(flashFor({ q: q({ source: "cache" }), now: NOW, lastFlashAt: 0, hidden: false })).toBeNull();
    expect(flashFor({ q: q({ direction: null }), now: NOW, lastFlashAt: 0, hidden: false })).toBeNull();
  });
});
