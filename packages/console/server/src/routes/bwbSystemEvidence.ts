import type { FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../config.js";
import { readBwbLiveGap, readBwbVol } from "../services/bwbEvidenceBridge.js";
import { readImpliedVarChecks, readQuoteHistoryCoverage } from "../services/dataEvidenceBridge.js";

/**
 * Bridged evidence reads for bwb and System › data (2026-10-12). Read-only; nothing here touches
 * an order.
 *
 * - `/api/bwb/live-gap` -- the live arm against paper's same arm per session (the `livegap` tab).
 * - `/api/bwb/vol` -- entry term structure and implied variance, `regime-split`, `iv-premium`.
 * - `/api/system/quote-history` -- the gex recorder's quote-history coverage via core.quotehistory.
 * - `/api/system/impliedvar` -- `core.impliedvar check` for VIX9D and VIX, now.
 */
export function registerBwbSystemEvidenceRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get("/api/bwb/live-gap", async () => readBwbLiveGap(config));
  app.get("/api/bwb/vol", async () => readBwbVol(config));
  app.get("/api/system/quote-history", async () => readQuoteHistoryCoverage(config));
  app.get("/api/system/impliedvar", async () => readImpliedVarChecks(config));
}
