import { useSearchParams } from "react-router-dom";
import type { TradingMode } from "@console/shared";
import { useBoolPref } from "./prefs";

/**
 * Paper/live page toggle persisted in the URL (?mode=live).
 *
 * The URL always wins — a link to a page is a link to the mode it names. Only when it says nothing
 * does the console preference decide, and that preference is read synchronously from the local
 * mirror so the first paint is already right: painting paper and flipping to live a moment later
 * would be worse than having no preference at all.
 */
export function useMode(): [TradingMode, (m: TradingMode) => void] {
  const [params, setParams] = useSearchParams();
  const defaultLive = useBoolPref("defaultLiveMode");
  const stated = params.get("mode");
  const mode: TradingMode = stated === "live" ? "live" : stated === "paper" ? "paper" : defaultLive ? "live" : "paper";
  const setMode = (m: TradingMode) => {
    // Both directions are stated explicitly now: with a live default, dropping the param would
    // mean "live" rather than "paper", so choosing paper has to say so.
    const next = new URLSearchParams(params);
    if (m === "live") next.set("mode", "live");
    else if (defaultLive) next.set("mode", "paper");
    else next.delete("mode");
    setParams(next, { replace: true });
  };
  return [mode, setMode];
}
