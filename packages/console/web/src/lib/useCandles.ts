import { useEffect, useState } from "react";
import type { CandleBar, CandlePeriod } from "@console/shared";
import { wsClient } from "./wsClient";

/** One candle series' bars, oldest first; mounting subscribes, unmounting releases. An empty symbol
 *  is a no-op (and no bars), so a caller can hold the series only while it needs it. */
export function useCandles(symbol: string, period: CandlePeriod): CandleBar[] {
  const [bars, setBars] = useState<CandleBar[]>([]);
  useEffect(() => {
    if (symbol === "") {
      setBars([]);
      return;
    }
    wsClient.acquireCandles(symbol, period);
    setBars(wsClient.getCandles(symbol, period));
    const off = wsClient.onCandles(symbol, period, () => setBars(wsClient.getCandles(symbol, period)));
    return () => {
      off();
      wsClient.releaseCandles(symbol, period);
    };
  }, [symbol, period]);
  return bars;
}
