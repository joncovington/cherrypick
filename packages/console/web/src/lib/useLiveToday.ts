import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import type { LiveTodayPayload, SuiteFeatures } from "@console/shared";
import { liveLoopsInUse, type LiveInUse } from "./visibility";

/**
 * Every live loop's day (`GET /api/live/today`), the Live page's `today` tab and its badge.
 *
 * Its own file, not the page's: the visibility rule for a live loop's tab reads it too, from the
 * rail, the frame and the router, and none of those should import a page to ask.
 */
export const LIVE_TODAY_KEY = ["live-today"] as const;

function useLiveTodayQuery(enabled: boolean) {
  return useQuery<LiveTodayPayload>({
    queryKey: LIVE_TODAY_KEY,
    queryFn: async () => {
      const res = await fetch("/api/live/today");
      if (!res.ok) throw new Error(`live today: HTTP ${res.status}`);
      return (await res.json()) as LiveTodayPayload;
    },
    refetchInterval: 15_000,
    enabled,
  });
}

export function useLiveToday() {
  return useLiveTodayQuery(true);
}

/**
 * The live loops Live Today reports anything for (see `liveLoopsInUse`), or null while that is
 * unknown. Asked only when some loop's live switch is explicitly off, since that is the only time a
 * Live tab could be hidden; otherwise it reads whatever the cache already holds and polls nothing.
 */
export function useLiveLoopsInUse(features: SuiteFeatures | undefined): LiveInUse {
  const anyOff = features !== undefined && features.ok && Object.values(features.live ?? {}).some((v) => v === false);
  const { data } = useLiveTodayQuery(anyOff);
  return useMemo(() => liveLoopsInUse(data), [data]);
}
