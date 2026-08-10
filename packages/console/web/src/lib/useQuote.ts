import { useEffect, useSyncExternalStore } from "react";
import { wsClient, type QuoteState, type WsState } from "./wsClient";

/** Live quote for one symbol; mounting subscribes, unmounting releases. */
export function useQuote(symbol: string): QuoteState | undefined {
  useEffect(() => {
    wsClient.acquire(symbol);
    return () => wsClient.release(symbol);
  }, [symbol]);
  return useSyncExternalStore(
    (cb) => wsClient.onQuote(symbol, cb),
    () => wsClient.getQuote(symbol),
  );
}

/** Connection state for the StatusHeader. */
export function useWsState(): WsState {
  return useSyncExternalStore(
    (cb) => wsClient.onState(cb),
    () => wsClient.getState(),
  );
}
