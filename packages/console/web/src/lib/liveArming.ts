import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ArmAttempt, LiveArmReadout, LiveArmRefusal, LiveArmingState } from "@console/shared";
import { csrfFetch, getJson } from "./api";
import { pushToast } from "./toast";

/**
 * The Live page's arm switches, browser half. Nothing here knows how to arm anything: it asks the
 * console's own routes (`routes/liveArming.ts`) for the module's readiness and posts the person's typed
 * confirmation back. The switch never moves on a click; it moves when the server's state says so.
 */

export const LIVE_ARMING_KEY = ["live-arming"] as const;

const IDLE_MS = 30_000;
const BUSY_MS = 4_000;
/** A finished attempt's notice is owed for this long, so a person who left the page is still told. */
const NOTICE_WINDOW_MS = 10 * 60_000;

export function useLiveArming() {
  return useQuery<LiveArmingState>({
    queryKey: LIVE_ARMING_KEY,
    queryFn: () => getJson<LiveArmingState>("/api/live/arming"),
    refetchInterval: (q) => (q.state.data?.attempts.some((a) => a.state === "running") ? BUSY_MS : IDLE_MS),
  });
}

/** True while any arm the server is running has not finished: the rail's amber dot. */
export function useArmingPending(): boolean {
  const { data } = useLiveArming();
  return data?.enabled === true && data.attempts.some((a) => a.state === "running");
}

/** The newest attempt for a loop, if any. */
export function latestAttempt(attempts: readonly ArmAttempt[], loop: string): ArmAttempt | null {
  const mine = attempts.filter((a) => a.loop === loop);
  return mine.length === 0 ? null : mine[mine.length - 1]!;
}

/**
 * A notice on whichever page the person is on when an arm finishes, from the server's attempts. Mounted
 * once, in the shell. An attempt is announced once per tab, and only while it is recent.
 */
export function useArmingNotices(): void {
  const { data } = useLiveArming();
  const told = useRef(new Set<string>());
  const qc = useQueryClient();
  useEffect(() => {
    if (data === undefined || !data.enabled) return;
    let changed = false;
    for (const a of data.attempts) {
      if (a.finishedAt === null || told.current.has(a.id)) continue;
      told.current.add(a.id);
      if (Date.now() - Date.parse(a.finishedAt) > NOTICE_WINDOW_MS) continue;
      changed = true;
      if (a.noop) continue;
      pushToast(
        a.state === "done"
          ? { tone: "exit", title: `${a.loop} is armed for today`, message: a.message, ttlMs: 10_000 }
          : { tone: "warn", title: `Arming ${a.loop} did not finish cleanly`, message: a.message, ttlMs: 30_000 },
      );
    }
    // The Live page's own readings change when an arm lands.
    if (changed) void qc.invalidateQueries({ queryKey: ["live-today"] });
  }, [data, qc]);
}

async function readBody<T>(res: Response): Promise<T> {
  return (await res.json().catch(() => ({}))) as T;
}

export async function fetchReadout(loop: string): Promise<LiveArmReadout> {
  const res = await fetch(`/api/live/arming/${encodeURIComponent(loop)}/readout`);
  const body = await readBody<LiveArmReadout & Partial<LiveArmRefusal>>(res);
  if (!res.ok) throw new Error(body.error ?? `HTTP ${String(res.status)}`);
  return body;
}

export type ArmReply =
  | { kind: "started"; attempt: ArmAttempt }
  | { kind: "changed"; refusal: LiveArmRefusal }
  | { kind: "refused"; refusal: LiveArmRefusal };

/** Post the person's confirmation. `typed` is exactly what is in the box: nothing is trimmed or defaulted. */
export async function postArm(loop: string, dialogId: string, typed: string): Promise<ArmReply> {
  const res = await csrfFetch(`/api/live/arming/${encodeURIComponent(loop)}/arm`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ confirm: typed, dialogId }),
  });
  const body = await readBody<{ attempt?: ArmAttempt } & Partial<LiveArmRefusal>>(res);
  if (res.status === 202 && body.attempt !== undefined) return { kind: "started", attempt: body.attempt };
  const refusal: LiveArmRefusal = { error: body.error ?? `HTTP ${String(res.status)}`, code: body.code ?? "error", ...(body.blockers ? { blockers: body.blockers } : {}), ...(body.readout ? { readout: body.readout } : {}) };
  return { kind: res.status === 409 && refusal.readout !== undefined && refusal.code === "readout_changed" ? "changed" : "refused", refusal };
}

export async function postDisarm(loop: string): Promise<{ ok: boolean; detail: string }> {
  const res = await csrfFetch(`/api/live/arming/${encodeURIComponent(loop)}/disarm`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ disarm: true }),
  });
  const body = await readBody<{ detail?: string; error?: string }>(res);
  return { ok: res.ok, detail: body.detail ?? body.error ?? `HTTP ${String(res.status)}` };
}
