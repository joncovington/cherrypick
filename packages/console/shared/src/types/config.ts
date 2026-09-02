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
  | "meic-risk";

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
  /** null = no readable config. Unknown must never be rendered as "off". */
  liveEnabled: boolean | null;
  /** flies only: whether the human attestation string is set (never its text). */
  gate0Confirmed?: boolean;
}

export interface LockStatusPayload {
  halted: boolean;
  haltFlagPath: string;
  modules: ModuleGateView[];
  fliesArm: { armed: boolean; date: string | null; at: string | null; stale: boolean };
  meicRiskDirty: boolean | null;
  sessionDate: string;
}

export interface ConfigSavePayload {
  ok: true;
  mtime: number | null;
  backup: string | null;
  issues: Array<[string, string]>;
}
