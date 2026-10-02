import type { SuiteFeatures } from "@console/shared";
import { isTradingModuleId } from "../lightbox/moduleOrder";

/**
 * Which pages, tabs and panels the console shows, from the suite's own answer to "what is on".
 *
 * Every rule here READS `features` (the orchestrator's `configcli` op, which folds the user's switch
 * and the machine's capabilities together) and decides nothing about the config itself. One rule
 * holds throughout: **unknown is visible.** Loading, a failed bridge (`ok: false`) and an id the
 * reply does not name all show the thing — a console that hides everything because the orchestrator
 * could not be asked is worse than one that shows a module that happens to be off.
 *
 * Config is never hidden: its module toggles are how an off module comes back.
 */

type Features = SuiteFeatures | undefined;

function known(f: Features): f is Extract<SuiteFeatures, { ok: true }> {
  return f !== undefined && f.ok;
}

/** A named suite feature (`advisor`, `technicals`, `review_narrative`, `morning_narrative`). */
export function isFeatureOn(name: string, f: Features): boolean {
  return !known(f) || f.features[name] !== false;
}

/**
 * Whether a page in the rail (a module id, or a suite surface) is shown.
 *
 * - a trading module: `modules.<id>.enabled`
 * - gex: the `gex-recorder` service
 * - advisor: the `advisor` feature
 * - anything else (Overview, Live, Reports, System, Config, the streamer): always.
 */
export function isModuleVisible(id: string, f: Features): boolean {
  if (!known(f)) return true;
  if (isTradingModuleId(id)) return f.modules[id]?.enabled !== false;
  if (id === "gex") return f.services["gex-recorder"] !== false;
  if (id === "advisor") return f.features["advisor"] !== false;
  return true;
}

/** Whether one tab of a visible page is shown: each module's advisor tab, and the technicals chart. */
export function isSlideVisible(module: string, slide: string, f: Features): boolean {
  if (slide === "advisor" && isTradingModuleId(module)) return isFeatureOn("advisor", f);
  if (module === "charts" && slide === "technicals") return isFeatureOn("technicals", f);
  return true;
}

export interface OffReason {
  /** One line for the turned-off card: why this page is not shown. */
  reason: string;
}

const CAPABILITY_LABEL: Record<string, string> = { dolt: "Dolt", claude: "Claude Code" };

/** Why a hidden page is off, in the words the turned-off card uses. `null` when it is visible. */
export function offReason(id: string, f: Features): OffReason | null {
  if (isModuleVisible(id, f) || !known(f)) return null;
  if (isTradingModuleId(id)) {
    const m = f.modules[id];
    if (m !== undefined && m.configured && m.missing.length > 0) {
      return { reason: `It is switched on, but needs ${m.missing.map((c) => CAPABILITY_LABEL[c] ?? c).join(" and ")}, which this machine does not have.` };
    }
    return { reason: "It is switched off in the suite config." };
  }
  if (id === "gex") return { reason: "The GEX recorder is switched off in the suite config." };
  if (id === "advisor") return { reason: "The advisor is switched off in the suite config." };
  return { reason: "It is switched off in the suite config." };
}
