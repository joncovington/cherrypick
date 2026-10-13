import { useQuery } from "@tanstack/react-query";
import type { ComparePayload, CompareScope } from "@console/shared";

/**
 * `GET /api/compare?scope=` -- every arm beside its control, from `core.compare`. The server memo
 * is stamped on the ledgers, so a poll is cheap; the answer only moves when a trade closes.
 */
export function useCompare(scope: CompareScope) {
  return useQuery<ComparePayload>({
    queryKey: ["compare", scope],
    queryFn: async () => {
      const url = `/api/compare?scope=${encodeURIComponent(scope)}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`${url}: HTTP ${String(res.status)}`);
      return (await res.json()) as ComparePayload;
    },
    // No placeholder from the previous key: the era's numbers shown under "all" while the wider
    // read loads would be the wrong scope wearing the right label.
    refetchInterval: 120_000,
  });
}
