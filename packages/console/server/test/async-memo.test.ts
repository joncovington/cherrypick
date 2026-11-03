import { describe, it, expect } from "vitest";
import { AsyncMemo } from "../src/services/asyncMemo.js";

/** The bridges' shared memo: a value per key for its TTL, and one load per key at a time. */

describe("AsyncMemo", () => {
  it("runs one load for concurrent requests of a key, and serves the rest from memory", async () => {
    const memo = new AsyncMemo<number>();
    let loads = 0;
    let release!: (v: number) => void;
    const load = () => {
      loads += 1;
      return new Promise<number>((res) => (release = res));
    };
    const a = memo.get("k", 1_000, 0, load);
    const b = memo.get("k", 1_000, 10, load);
    await new Promise((res) => setTimeout(res, 0)); // the load starts on the next microtask
    release(7);
    expect(await Promise.all([a, b])).toEqual([7, 7]);
    expect(await memo.get("k", 1_000, 500, load)).toBe(7);
    expect(loads).toBe(1);
  });

  it("loads again once the TTL has passed, and keeps keys apart", async () => {
    const memo = new AsyncMemo<string>();
    let loads = 0;
    const load = (v: string) => () => {
      loads += 1;
      return v;
    };
    expect(await memo.get("a", 1_000, 0, load("one"))).toBe("one");
    expect(await memo.get("b", 1_000, 0, load("other"))).toBe("other");
    expect(await memo.get("a", 1_000, 1_000, load("two"))).toBe("two");
    expect(loads).toBe(3);
  });

  it("does not remember a load that threw, so the next request tries again", async () => {
    const memo = new AsyncMemo<number>();
    await expect(
      memo.get("k", 1_000, 0, () => {
        throw new Error("boom");
      }),
    ).rejects.toThrow("boom");
    expect(await memo.get("k", 1_000, 1, () => 3)).toBe(3);
  });
});
