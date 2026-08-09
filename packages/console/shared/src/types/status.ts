/** Freshness of one data source, shown in the StatusHeader. */
export interface SourceFreshness {
  /** Stable identifier, e.g. "streamer", "meic.paper", "gex". */
  key: string;
  label: string;
  /** Seconds since the source last wrote; null when the source is absent. */
  ageSeconds: number | null;
  present: boolean;
}

export type MarketDataState = "live" | "cached" | "disconnected";

export interface StatusPayload {
  /** Server clock, ISO-8601 with offset. */
  now: string;
  /** Eastern-time wall clock string the market clock renders from. */
  nowEt: string;
  marketData: MarketDataState;
  /** The suite credential's detected scope; null = no credential or never probed. */
  credentialScope: "read" | "trade" | null;
  sources: SourceFreshness[];
}

export type TradingMode = "live" | "paper";
