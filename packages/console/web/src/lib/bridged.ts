import { useQuery } from "@tanstack/react-query";
import type { BridgedResult } from "@console/shared";

/**
 * One bridged read: a module's own JSON through a server bridge (`BridgedResult`). A failed read
 * comes back as `{ok: false, error}` and is shown as that error, never as an empty table -- the
 * subprocess not running and the module answering "nothing yet" are different facts. `refetchMs`
 * may read the last answer, so a page can poll fast only while something is moving.
 */
export function useBridged<T>(
  key: readonly unknown[],
  url: string,
  refetchMs: number | ((data: BridgedResult<T> | undefined) => number) = 60_000,
) {
  return useQuery<BridgedResult<T>>({
    queryKey: ["bridged", ...key],
    queryFn: async () => {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`${url}: HTTP ${String(res.status)}`);
      return (await res.json()) as BridgedResult<T>;
    },
    refetchInterval: typeof refetchMs === "number" ? refetchMs : (query) => refetchMs(query.state.data),
  });
}

/** A signed per-share price gap, `+0.15` / `-0.10`; a null is an em dash. */
export function fmtGap(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : v < 0 ? "-" : ""}${Math.abs(v).toFixed(digits)}`;
}

/** The tone class for a signed number: the sign alone, never a threshold. */
export function signTone(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "muted";
  return v > 0 ? "pnl-pos" : "pnl-neg";
}
