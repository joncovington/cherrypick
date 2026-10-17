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
 * **One exception, which fails closed: the Backtest page** (`FAIL_CLOSED_PAGES`). Its results come
 * from licensed vendor history whose licence forbids redistribution, so showing it by mistake is a
 * breach, not an inconvenience. It is shown only when the reply was read AND names
 * `features.backtest` as exactly `true` — hidden while loading, on a failed bridge, when the key is
 * absent and when it is false. A hidden fail-closed page gives no off reason, and a direct URL to
 * it is the 404 page (`ModuleRoute`), so nothing on the screen says it exists. The server refuses
 * its routes on its own (`routes/backtest.ts`); this rule is presentation, not the guard.
 *
 * Config is never hidden: its module toggles are how an off module comes back.
 */

type Features = SuiteFeatures | undefined;

function known(f: Features): f is Extract<SuiteFeatures, { ok: true }> {
  return f !== undefined && f.ok;
}

/** The pages that fail closed, each gated on the suite feature of the same name. */
export const FAIL_CLOSED_PAGES: ReadonlySet<string> = new Set(["backtest"]);

export function isFailClosed(id: string): boolean {
  return FAIL_CLOSED_PAGES.has(id);
}

/** A fail-closed page that is not shown: its route must behave as if it did not exist. */
export function isHiddenAsUnknown(id: string, f: Features): boolean {
  return isFailClosed(id) && !isModuleVisible(id, f);
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
 * - flow: the `options_flow` feature (`quikoptions.enabled`)
 * - backtest: ONLY an explicit `features.backtest === true` (fail closed, above)
 * - anything else (Live, Reports, System, Config, the streamer): always.
 */
export function isModuleVisible(id: string, f: Features): boolean {
  if (isFailClosed(id)) return known(f) && f.features[id] === true;
  if (!known(f)) return true;
  if (isTradingModuleId(id)) return f.modules[id]?.enabled !== false;
  if (id === "gex") return f.services["gex-recorder"] !== false;
  if (id === "advisor") return f.features["advisor"] !== false;
  if (id === "flow") return f.features["options_flow"] !== false;
  return true;
}

/** Whether one tab of a visible page is shown: each module's advisor tab, and the technicals chart.
 *  Every tab of a fail-closed page follows the page. */
export function isSlideVisible(module: string, slide: string, f: Features): boolean {
  // A hidden fail-closed page has no tabs either, so neither the rail nor `j`/`k` can name one.
  if (isFailClosed(module)) return isModuleVisible(module, f);
  if (slide === "advisor" && isTradingModuleId(module)) return isFeatureOn("advisor", f);
  if (module === "charts" && (slide === "technicals" || slide === "setups")) return isFeatureOn("technicals", f);
  return true;
}

export interface OffReason {
  /** One line for the turned-off card: why this page is not shown. */
  reason: string;
}

const CAPABILITY_LABEL: Record<string, string> = { dolt: "Dolt", claude: "Claude Code" };

/** Why a hidden page is off, in the words the turned-off card uses. `null` when it is visible, and
 *  always `null` for a fail-closed page: it has no off card to explain, only a 404. */
export function offReason(id: string, f: Features): OffReason | null {
  if (isFailClosed(id)) return null;
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
  if (id === "flow") return { reason: "The QuikOptions capture is switched off in the suite config (quikoptions.enabled)." };
  return { reason: "It is switched off in the suite config." };
}
