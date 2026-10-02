import { useQuery } from "@tanstack/react-query";
import type { SuiteFeatures } from "@console/shared";

/**
 * What the suite has turned on (`GET /api/features`, the orchestrator's `configcli features`).
 *
 * Its own file, not `api.ts`: page tests mock `api.ts` wholesale, and every component that hides an
 * off module calls this. `data` is undefined while loading and after an error; every rule in
 * `visibility.ts` reads that as visible.
 */
export const FEATURES_KEY = ["features"] as const;

function useFeaturesQuery() {
  return useQuery<SuiteFeatures>({
    queryKey: FEATURES_KEY,
    queryFn: async () => {
      const res = await fetch("/api/features");
      if (!res.ok) throw new Error(`/api/features: HTTP ${res.status}`);
      return (await res.json()) as SuiteFeatures;
    },
    refetchInterval: 15_000,
    staleTime: 10_000,
  });
}

export function useFeatures(): SuiteFeatures | undefined {
  return useFeaturesQuery().data;
}

/**
 * Why the console could not learn what is on, or null when it could (or is still asking). The
 * status bar shows this as a warning chip: everything is being shown because nothing could be hidden
 * safely, which is worth saying but never worth blanking a page over.
 */
export function useFeaturesFailure(): string | null {
  const q = useFeaturesQuery();
  if (q.data !== undefined && !q.data.ok) return q.data.error;
  if (q.data === undefined && q.isError) return (q.error as Error).message;
  return null;
}
