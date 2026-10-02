import type { MarketDataState } from "./status.js";

/** Candle widths the live chart offers. Extended hours always: an overnight futures chart is the point. */
export const CANDLE_PERIODS = ["1m", "5m", "15m"] as const;
export type CandlePeriod = (typeof CANDLE_PERIODS)[number];

/** Browser → server. */
export type ClientMessage =
  | { op: "subscribe"; symbols: string[] }
  | { op: "unsubscribe"; symbols: string[] }
  | { op: "candles"; symbol: string; period: CandlePeriod }
  | { op: "candlesOff"; symbol: string; period: CandlePeriod };

/** One bar. `t` is the bar's START in epoch seconds; the bar in progress is re-sent with the same `t`. */
export interface CandleBar {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

/**
 * Bars for one series. `replace: true` is the whole series (draw it from scratch): the history
 * snapshot, a re-snapshot after a reconnect, or a bar the feed removed. Otherwise `bars` are new or
 * changed bars, oldest first.
 */
export interface CandleMessage {
  type: "candles";
  symbol: string;
  period: CandlePeriod;
  replace: boolean;
  bars: CandleBar[];
}

export interface QuoteTick {
  type: "tick";
  symbol: string;
  /** Which fields changed is up to the event — unchanged fields are omitted. */
  bid?: number;
  ask?: number;
  last?: number;
  dayVolume?: number;
  /** The prior session's close from DXLink's Summary event — for a future, its settle. Only the
   *  console's own feed carries it; a streamer-cache snapshot never does, so a change measured
   *  against it is always the feed's own, never a stale cache row's. */
  prevClose?: number;
  /** Source of this value: the console's own DXLink session or the streamer cache. */
  source: "dxlink" | "cache";
  ts: number;
}

export interface WsStatus {
  type: "status";
  marketData: MarketDataState;
  /** DXLink connection detail for the header tooltip. */
  dxlink: "disconnected" | "connecting" | "connected" | "error";
  ts: number;
}

/** Server → browser. */
export type ServerMessage = QuoteTick | WsStatus | CandleMessage;
