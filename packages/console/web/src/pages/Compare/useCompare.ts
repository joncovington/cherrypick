import { useQuery } from "@tanstack/react-query";
import type { ComparePayload, CompareScope } from "@console/shared";
import { getJson } from "../../lib/api";

/**
 * `GET /api/compare?scope=` -- every arm beside its control, from `core.compare`. The server memo
 * is stamped on the ledgers, so a poll is cheap; the answer only moves when a trade closes.
 */
export function useCompare(scope: CompareScope) {
  return useQuery<ComparePayload>({
    queryKey: ["compare", scope],
    queryFn: async () => {
      const url = `/api/compare?scope=${encodeURIComponent(scope)}`;
      return getJson<ComparePayload>(url);
    },
    // No placeholder from the previous key: the era's numbers shown under "all" while the wider
    // read loads would be the wrong scope wearing the right label.
    refetchInterval: 120_000,
  });
}
