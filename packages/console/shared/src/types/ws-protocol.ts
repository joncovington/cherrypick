import type { MarketDataState } from "./status.js";

/** Browser → server. */
export type ClientMessage =
  | { op: "subscribe"; symbols: string[] }
  | { op: "unsubscribe"; symbols: string[] };

export interface QuoteTick {
  type: "tick";
  symbol: string;
  /** Which fields changed is up to the event — unchanged fields are omitted. */
  bid?: number;
  ask?: number;
  last?: number;
  dayVolume?: number;
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
export type ServerMessage = QuoteTick | WsStatus;
