import { useSearchParams } from "react-router-dom";
import type { TradingMode } from "@console/shared";

/** Paper/live page toggle persisted in the URL (?mode=live). Defaults to paper. */
export function useMode(): [TradingMode, (m: TradingMode) => void] {
  const [params, setParams] = useSearchParams();
  const mode: TradingMode = params.get("mode") === "live" ? "live" : "paper";
  const setMode = (m: TradingMode) => {
    const next = new URLSearchParams(params);
    if (m === "live") next.set("mode", "live");
    else next.delete("mode");
    setParams(next, { replace: true });
  };
  return [mode, setMode];
}
