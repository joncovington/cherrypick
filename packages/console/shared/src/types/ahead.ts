/**
 * The sessions ahead for one arm (`core.ahead`, through the module's own `ahead` command): the next
 * sessions with what each of its dated gates does there, the gates that read a value at the time, and
 * its record on each gate's kind of day. Computed in Python; the console renders it.
 */

export const AHEAD_MODULES = ["meic", "automations"] as const;
export type AheadModule = (typeof AHEAD_MODULES)[number];
export function isAheadModule(m: string): m is AheadModule {
  return (AHEAD_MODULES as readonly string[]).includes(m);
}

export interface AheadGate {
  label: string;
  name: string | null;
  effect: string | null;
  depends_on: string | null;
}

export interface AheadValueGate {
  name: string | null;
  effect: string | null;
  depends_on: string | null;
}

export interface AheadSession {
  session: string;
  weekday: string;
  close: string;
  early_close: boolean;
  /** Weekdays the market is closed between the previous listed session and this one. */
  closed_before: string[];
  gates: AheadGate[];
  /** Labels the module's calendar does not answer for this day. */
  unknown: string[];
}

/** The arm's record on one gate's kind of day; net and means only once `readable`. */
export interface AheadRecord {
  label: string;
  name: string | null;
  sessions: number;
  other_sessions: number;
  unknown_sessions: number;
  readable: boolean;
  to_go: number;
  net: number | null;
  mean: number | null;
  other_mean: number | null;
}

export interface SessionsAhead {
  module: string;
  arm: string;
  era_start: string | null;
  start: string | null;
  min_sessions: number | null;
  sessions: AheadSession[];
  gates: AheadGate[];
  value_gates: AheadValueGate[];
  records: AheadRecord[] | null;
  error: string | null;
}
