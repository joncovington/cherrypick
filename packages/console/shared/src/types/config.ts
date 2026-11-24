/**
 * The Config page's payloads.
 *
 * The suite has no JSON schema for its configs — a module's schema lives in the module, and the
 * orchestrator's editor validates root shape only. So the server ships whole documents and the web
 * side's field metadata decides what is editable; anything the metadata doesn't name simply isn't
 * rendered, which is what keeps this page to the handful of settings that actually change.
 */

export type ConfigTargetId =
  | "orchestrator"
  | "meic"
  | "flies"
  | "gex"
  | "earnings"
  | "streamer"
  | "meic-risk"
  | "manage";

export interface GuardedPointer {
  /** RFC 6901 pointer the config editor refuses to write, in either direction. */
  pointer: string;
  /** Where the real path is — shown beside the locked field instead of a control. */
  hint: string;
}

export interface ConfigTargetModel {
  exists: boolean;
  portable?: string | null;
  doc: Record<string, unknown> | null;
  /** The version staged edits are saved against; a mismatch on save is a 409, never a clobber. */
  mtime: number | null;
  guarded: GuardedPointer[];
  issues: Array<[string, string]>;
  /** Set when this one target failed to load — the rest of the page still renders. */
  error?: string;
}

export interface ConfigModelPayload {
  targets: Record<ConfigTargetId, ConfigTargetModel>;
}

export interface ModuleGateView {
  id: string;
  /** null = a module with a live path whose switch cannot be read. Unknown must never render as "off". */
  liveEnabled: boolean | null;
  /** Whether the module has a live loop at all. False = paper by construction. */
  livePath: boolean;
  /** `modules.<id>.enabled` in the orchestrator config; null when the module is not listed there. */
  moduleEnabled: boolean | null;
  /** flies only: whether the human attestation string is set (never its text). */
  gate0Confirmed?: boolean;
}

export interface LockStatusPayload {
  halted: boolean;
  haltFlagPath: string;
  modules: ModuleGateView[];
  fliesArm: { armed: boolean; date: string | null; at: string | null; stale: boolean };
  sessionDate: string;
}

export interface ConfigSavePayload {
  ok: true;
  mtime: number | null;
  backup: string | null;
  issues: Array<[string, string]>;
}

// ------------------------------------------------------------------- the live arm (Config > live tab)
// Which arm each live loop runs. `GET /api/config/live-arm` composes the orchestrator's own answer
// (`configcli` `live_arm_options`: the arm live runs, the arms with a paper twin, and why a change is
// refused right now) with the files that name the loops the page cannot change. The change itself is
// `POST /api/config/live-arm` -> `set_live_arm`; the console holds none of its rules.

/** A plain setting of an arm, as the registry holds it (nested values are left out). */
export type LiveArmSetting = string | number | boolean | null;

export interface LiveArmChoice {
  arm: string;
  /** The paper twin that measures this arm (`shadow_of`). */
  twin: string;
  /** The arm's own plain settings, for showing what a change would change. */
  settings: Record<string, LiveArmSetting>;
}

export interface LiveArmLoop {
  /** `meic` (the main login), `meic.<login>` or `ladder`. */
  loop: string;
  label: string;
  symbol: string | null;
  /** The arm the loop runs now; null when `live.arm` is unset (it trades the top level). */
  current: string | null;
  /** Only MEIC's main login can be changed from here. */
  editable: boolean;
  /** Why this loop cannot be changed here, in words; null when it can. */
  note: string | null;
  /** The arms the loop may run, each with its twin (empty for a loop that is not editable). */
  arms: LiveArmChoice[];
  /** Why a change is refused right now (not flat, not settled, ...); empty when it would be allowed. */
  blockers: string[];
  canChange: boolean;
}

export interface LiveArmPayload {
  loops: LiveArmLoop[];
  /** `unavailable`: the orchestrator bridge could not answer, so nothing here can be changed. */
  bridge: "ok" | "unavailable";
  error: string | null;
}

export interface LiveArmChangePayload {
  ok: true;
  /** False when the arm asked for was already the live arm. */
  changed: boolean;
  from: string | null;
  arm: string;
  note: string | null;
  /** The page's own read, after the change. */
  payload: LiveArmPayload;
}
