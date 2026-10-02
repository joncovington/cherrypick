/**
 * What the suite config has turned on, as the orchestrator resolves it (`configcli` op `features`).
 *
 * The console never decides "off" itself: a module is off when Python says `enabled: false`, which
 * already folds in the user's switch AND any capability the module needs (Dolt, Claude Code). The
 * console only reads the answer, and when it cannot read one it shows everything (fail open).
 */

export interface SuiteModuleFeature {
  /** The user's own switch in the suite config. */
  configured: boolean;
  /** The effective answer: switched on AND every required capability present. */
  enabled: boolean;
  /** Why a configured module is still off: the capabilities this machine lacks ("dolt", "claude"). */
  missing: string[];
}

export interface SuiteFeaturesOk {
  ok: true;
  capabilities: Record<string, boolean>;
  modules: Record<string, SuiteModuleFeature>;
  services: Record<string, boolean>;
  features: Record<string, boolean>;
}

export interface SuiteFeaturesFailure {
  ok: false;
  error: string;
}

export type SuiteFeatures = SuiteFeaturesOk | SuiteFeaturesFailure;
