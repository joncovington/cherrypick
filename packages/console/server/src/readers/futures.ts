import fs from "node:fs";
import path from "node:path";
import type { FuturesTickerEntry, FuturesTickerPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";

/** The Overview's futures ticker, in display order. */
const TICKER: { product: string; name: string }[] = [
  { product: "ES", name: "S&P 500 e-mini" },
  { product: "NQ", name: "Nasdaq-100 e-mini" },
  { product: "CL", name: "WTI crude" },
  { product: "GC", name: "Gold" },
  { product: "ZB", name: "30-year Treasury bond" },
];

/** A map older than this names contracts that may have rolled — the gex recorder's and the morning
 *  pack's bound. Past it the ticker shows no symbol rather than quoting a rolled-off contract. */
const MAX_AGE_DAYS = 5;

interface MapRow {
  symbol?: unknown;
  streamer_symbol?: unknown;
  expiration?: unknown;
}

interface ContractMap {
  refreshed_at?: unknown;
  contracts?: Record<string, MapRow[] | undefined>;
}

function readMap(config: ConsoleConfig): ContractMap | null {
  try {
    return JSON.parse(
      fs.readFileSync(path.join(config.paths.cherrypick, "state", "futures_contracts.json"), "utf-8"),
    ) as ContractMap;
  } catch {
    return null;
  }
}

/** Product → contract for the ticker, from `state/futures_contracts.json`. Never assembles a symbol:
 *  the exchange suffix is only knowable from the broker, so an unresolved product is a gap. */
export function readFuturesTicker(config: ConsoleConfig, now: Date = new Date()): FuturesTickerPayload {
  const raw = readMap(config);
  const refreshedAt = typeof raw?.refreshed_at === "string" ? raw.refreshed_at : null;
  const refreshedMs = refreshedAt !== null ? Date.parse(refreshedAt) : NaN;
  const mapReason =
    raw === null || Number.isNaN(refreshedMs)
      ? "no_map"
      : (now.getTime() - refreshedMs) / 86_400_000 > MAX_AGE_DAYS
        ? "map_stale"
        : null;

  const entries: FuturesTickerEntry[] = TICKER.map(({ product, name }) => {
    const row = raw?.contracts?.[product]?.[0];
    const streamerSymbol = typeof row?.streamer_symbol === "string" ? row.streamer_symbol : null;
    const reason = mapReason ?? (streamerSymbol === null ? "not_in_map" : null);
    return {
      product,
      label: `/${product}`,
      name,
      contract: typeof row?.symbol === "string" ? row.symbol : null,
      streamerSymbol: reason === null ? streamerSymbol : null,
      expiration: typeof row?.expiration === "string" ? row.expiration : null,
      reason,
    };
  });
  return { refreshedAt, entries };
}
