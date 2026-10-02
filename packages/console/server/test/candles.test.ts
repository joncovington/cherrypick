import { EventEmitter } from "node:events";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CandleMessage } from "@console/shared";
import { CandleService, isCandleSymbol, parseCandleSymbol, type CandleFeed } from "../src/market/candles.js";

/**
 * The live candle series, driven by a fake feed shaped like what `scripts/probe_candles.py`
 * recorded from /ES on 2026-10-01: a `{=1m}` subscription answered as `{=m}`, the history as one
 * snapshot newest first between SNAPSHOT_BEGIN and SNAPSHOT_END, a removal sentinel with no prices
 * closing it, then the bar in progress re-sent under its own time.
 */

const SYM = "/ESZ26:XCME";
const BEGIN = 0x04;
const END = 0x08;
const REMOVE = 0x02;
const T0 = Date.UTC(2026, 9, 2, 3, 20); // 23:20 ET

class FakeFeed extends EventEmitter {
  added: Array<{ symbol: string; fromTime: number }> = [];
  removed: string[] = [];
  async addCandleSubscription(symbol: string, fromTime: number): Promise<void> {
    this.added.push({ symbol, fromTime });
  }
  removeCandleSubscription(symbol: string): void {
    this.removed.push(symbol);
  }
}

function setup() {
  const feed = new FakeFeed();
  const svc = new CandleService(feed as unknown as CandleFeed);
  const out: CandleMessage[] = [];
  svc.on("candles", (m: CandleMessage) => out.push(m));
  return { feed, svc, out };
}

function candle(label: string, minute: number, close: number, flags = 0, extra: Record<string, unknown> = {}) {
  return {
    eventType: "Candle",
    eventSymbol: label,
    eventFlags: flags,
    time: T0 + minute * 60_000,
    open: 7741,
    high: Math.max(7741, close),
    low: Math.min(7741, close),
    close,
    volume: 10,
    ...extra,
  };
}

const flush = (): Promise<void> => new Promise((r) => setImmediate(r));

afterEach(() => vi.useRealTimers());

describe("candle symbols", () => {
  it("reads the period the feed labelled, including dxFeed's dropped multiplier of 1", () => {
    expect(parseCandleSymbol(`${SYM}{=m}`)).toEqual({ symbol: SYM, period: "1m" });
    expect(parseCandleSymbol(`${SYM}{=1m}`)).toEqual({ symbol: SYM, period: "1m" });
    expect(parseCandleSymbol(`${SYM}{=5m}`)).toEqual({ symbol: SYM, period: "5m" });
    expect(parseCandleSymbol(`${SYM}{=15m}`)).toEqual({ symbol: SYM, period: "15m" });
  });

  it("is not ours: a period we do not offer, extra attributes, or no attributes", () => {
    expect(parseCandleSymbol(`${SYM}{=2m}`)).toBeNull();
    expect(parseCandleSymbol(`${SYM}{=5m,tho=true}`)).toBeNull();
    expect(parseCandleSymbol(SYM)).toBeNull();
  });

  it("a browser cannot smuggle candle attributes into the subscription", () => {
    expect(isCandleSymbol(SYM)).toBe(true);
    expect(isCandleSymbol(`${SYM}{=1m,price=bid}`)).toBe(false);
    expect(isCandleSymbol("")).toBe(false);
  });
});

describe("a series' history", () => {
  it("subscribes the symbol with its period, extended hours (no tho), from the lookback", () => {
    const { feed, svc } = setup();
    const before = Date.now();
    svc.acquire(SYM, "1m");
    expect(feed.added).toHaveLength(1);
    expect(feed.added[0]!.symbol).toBe(`${SYM}{=1m}`);
    expect(feed.added[0]!.fromTime).toBeLessThanOrEqual(before - 86_400_000 + 1000);
  });

  it("is buffered until the snapshot ends, then sent whole, oldest first, without the sentinel", () => {
    const { feed, svc, out } = setup();
    svc.acquire(SYM, "1m");
    // Newest first, labelled {=m}, as the feed sends it.
    feed.emit("feed", candle(`${SYM}{=m}`, 2, 7742, BEGIN));
    feed.emit("feed", candle(`${SYM}{=m}`, 1, 7740));
    feed.emit("feed", candle(`${SYM}{=m}`, 0, 7741));
    expect(out).toHaveLength(0);
    expect(svc.snapshot(SYM, "1m")).toBeNull();
    feed.emit("feed", candle(`${SYM}{=m}`, -5, NaN, REMOVE | END, { open: NaN, high: NaN, low: NaN }));
    expect(out).toHaveLength(1);
    expect(out[0]!.replace).toBe(true);
    expect(out[0]!.period).toBe("1m");
    expect(out[0]!.bars.map((b) => b.c)).toEqual([7741, 7740, 7742]);
    expect(out[0]!.bars.every((b) => b.o > 0)).toBe(true);
    expect(svc.snapshot(SYM, "1m")?.bars).toHaveLength(3);
  });

  it("a bar without a full positive OHLC is never drawn, even when nothing flags it removed", () => {
    // The sentinel above is caught by its REMOVE flag; this is the other door — a zero- or
    // NaN-filled placeholder, which a chart would draw as a bar to zero.
    const { feed, svc, out } = setup();
    svc.acquire(SYM, "1m");
    feed.emit("feed", candle(`${SYM}{=m}`, 1, 7742, BEGIN));
    feed.emit("feed", candle(`${SYM}{=m}`, 0, 0, 0, { open: 0, high: 0, low: 0 }));
    feed.emit("feed", candle(`${SYM}{=m}`, -1, NaN, END, { open: "NaN", high: "NaN", low: "NaN" }));
    expect(out[0]!.bars.map((b) => b.c)).toEqual([7742]);
  });

  it("a snapshot that never ends is declared complete after a quiet spell", () => {
    vi.useFakeTimers();
    const { feed, svc, out } = setup();
    svc.acquire(SYM, "5m");
    feed.emit("feed", candle(`${SYM}{=5m}`, 0, 7741, BEGIN));
    vi.advanceTimersByTime(4_999);
    expect(out).toHaveLength(0);
    vi.advanceTimersByTime(1);
    expect(out).toHaveLength(1);
    expect(out[0]!.replace).toBe(true);
  });

  it("a contract with no bars at all still answers, with an empty series", () => {
    vi.useFakeTimers();
    const { svc, out } = setup();
    svc.acquire(SYM, "5m");
    vi.advanceTimersByTime(5_000);
    expect(out).toEqual([{ type: "candles", symbol: SYM, period: "5m", replace: true, bars: [] }]);
  });
});

describe("live bars", () => {
  async function loaded() {
    const s = setup();
    s.svc.acquire(SYM, "1m");
    s.feed.emit("feed", candle(`${SYM}{=m}`, 0, 7741, BEGIN | END));
    s.out.length = 0;
    return s;
  }

  it("the bar in progress is replaced in place, then a new time opens the next bar", async () => {
    const { feed, out } = await loaded();
    feed.emit("feed", candle(`${SYM}{=m}`, 0, 7742));
    await flush();
    feed.emit("feed", candle(`${SYM}{=m}`, 1, 7743));
    await flush();
    expect(out.map((m) => m.replace)).toEqual([false, false]);
    expect(out[0]!.bars).toEqual([expect.objectContaining({ t: (T0 / 1000) | 0, c: 7742 })]);
    expect(out[1]!.bars).toEqual([expect.objectContaining({ t: (T0 + 60_000) / 1000, c: 7743 })]);
  });

  it("one batch of updates is one message per series", async () => {
    const { feed, out } = await loaded();
    feed.emit("feed", candle(`${SYM}{=m}`, 0, 7742));
    feed.emit("feed", candle(`${SYM}{=m}`, 0, 7743));
    feed.emit("feed", candle(`${SYM}{=m}`, 1, 7744));
    await flush();
    expect(out).toHaveLength(1);
    expect(out[0]!.bars.map((b) => b.c)).toEqual([7743, 7744]);
  });

  it("a bar the feed removes after the snapshot redraws the series", async () => {
    const { feed, svc, out } = await loaded();
    feed.emit("feed", candle(`${SYM}{=m}`, 0, NaN, REMOVE));
    await flush();
    expect(out).toEqual([expect.objectContaining({ replace: true, bars: [] })]);
    expect(svc.snapshot(SYM, "1m")?.bars).toEqual([]);
  });

  it("a re-snapshot (the feed resubscribed) is buffered and sent whole again", async () => {
    const { feed, out } = await loaded();
    feed.emit("feed", candle(`${SYM}{=m}`, 1, 7745, BEGIN));
    await flush();
    expect(out).toHaveLength(0);
    feed.emit("feed", candle(`${SYM}{=m}`, 0, 7741, END));
    expect(out).toHaveLength(1);
    expect(out[0]!.replace).toBe(true);
    expect(out[0]!.bars.map((b) => b.c)).toEqual([7741, 7745]);
  });

  it("events for a series nobody holds, or another period, are ignored", async () => {
    const { feed, out } = await loaded();
    feed.emit("feed", candle(`${SYM}{=5m}`, 0, 7799));
    feed.emit("feed", candle(`/NQZ26:XCME{=m}`, 0, 7799));
    feed.emit("feed", { eventType: "Quote", eventSymbol: `${SYM}{=m}`, bidPrice: 1 });
    await flush();
    expect(out).toHaveLength(0);
  });
});

describe("holding a series", () => {
  it("lingers after the last viewer, then removes the Candle subscription it added", () => {
    vi.useFakeTimers();
    const { feed, svc } = setup();
    svc.acquire(SYM, "1m");
    svc.acquire(SYM, "1m");
    svc.release(SYM, "1m");
    svc.release(SYM, "1m");
    vi.advanceTimersByTime(29_999);
    expect(feed.removed).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(feed.removed).toEqual([`${SYM}{=1m}`]);
  });

  it("a viewer returning inside the linger keeps the series and does not resubscribe", () => {
    vi.useFakeTimers();
    const { feed, svc } = setup();
    svc.acquire(SYM, "5m");
    svc.release(SYM, "5m");
    vi.advanceTimersByTime(10_000);
    svc.acquire(SYM, "5m");
    vi.advanceTimersByTime(60_000);
    expect(feed.removed).toEqual([]);
    expect(feed.added).toHaveLength(1);
  });

  it("after a feed rebuild, resubscribes from the newest bar it holds", () => {
    const { feed, svc } = setup();
    svc.acquire(SYM, "5m");
    feed.emit("feed", candle(`${SYM}{=5m}`, 10, 7741, BEGIN | END));
    feed.emit("state", "disconnected");
    feed.emit("state", "connected");
    expect(feed.added).toHaveLength(2);
    expect(feed.added[1]).toEqual({ symbol: `${SYM}{=5m}`, fromTime: T0 + 10 * 60_000 });
  });
});
