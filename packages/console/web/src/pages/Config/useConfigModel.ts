import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { ConfigModelPayload, ConfigSavePayload, ConfigTargetId, LockStatusPayload } from "@console/shared";
import { getCsrf } from "../../lib/api";

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** An error carrying what the server said about WHY, so the UI can be specific instead of "failed". */
export class ConfigError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null,
  ) {
    super(message);
  }
}

/**
 * Like `mutateJson`, but it keeps the server's explanation. A guarded pointer, a stale file and a
 * validation error need different words on screen, and the shared helper throws away the body that
 * distinguishes them. One request either way — a write that failed must not be quietly retried.
 */
async function postConfig<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", "x-csrf-token": await getCsrf() },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const payload = (await res.json().catch(() => ({}))) as { error?: string; code?: string };
    throw new ConfigError(payload.error ?? `HTTP ${String(res.status)}`, res.status, payload.code ?? null);
  }
  return (await res.json()) as T;
}

/**
 * The whole editable model. Not polled — each target is a subprocess on the server, and a config
 * file does not change under you often enough to be worth the churn. Refetched on focus and after
 * every save, and the mtime it carries is what makes a concurrent edit a 409 rather than a clobber.
 */
export function useConfigModel() {
  return useQuery<ConfigModelPayload>({
    queryKey: ["config-model"],
    queryFn: () => getJson<ConfigModelPayload>("/api/config/model"),
    refetchOnWindowFocus: true,
    staleTime: 10_000,
  });
}

/** The lock hero's read — file-only on the server, so polling it is cheap. */
export function useLockStatus() {
  return useQuery<LockStatusPayload>({
    queryKey: ["config-lock"],
    queryFn: () => getJson<LockStatusPayload>("/api/config/lock"),
    refetchInterval: 5_000,
  });
}

export function useSetLock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { present: boolean; confirm?: string }) =>
      postConfig<LockStatusPayload>("/api/config/lock", vars),
    onSuccess: (status) => {
      qc.setQueryData(["config-lock"], status);
      // The System card shows the same flag.
      void qc.invalidateQueries({ queryKey: ["system"] });
      void qc.invalidateQueries({ queryKey: ["overview"] });
    },
  });
}

export function useSaveSection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      target: ConfigTargetId;
      expectedMtime: number | null;
      edits: Array<{ pointer: string; value: unknown }>;
    }) => postConfig<ConfigSavePayload>("/api/config/save", vars),
    onSettled: () => {
      // Refetch either way: on success to pick up the new mtime, on a conflict because the whole
      // point of that failure is that our copy is behind.
      void qc.invalidateQueries({ queryKey: ["config-model"] });
      void qc.invalidateQueries({ queryKey: ["config-lock"] });
    },
  });
}

export function usePrefs() {
  return useQuery<{ prefs: Record<string, unknown> }>({
    queryKey: ["config-prefs"],
    queryFn: () => getJson<{ prefs: Record<string, unknown> }>("/api/config/prefs"),
  });
}

export function useSetPref() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { key: string; value: unknown }) =>
      postConfig<{ prefs: Record<string, unknown> }>("/api/config/prefs", vars),
    onSuccess: (data) => qc.setQueryData(["config-prefs"], data),
  });
}
