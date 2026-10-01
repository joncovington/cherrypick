import { lazy, type ComponentType } from "react";
import type { ModuleId } from "./moduleOrder";

/**
 * Every page's manifest, rendered inside the module frame (`ModuleFrame`): a left rail and a
 * content pane in the shell's own outlet.
 *
 * The suite moved onto the frame one module at a time (2026-09-22 to 2026-09-25), with the
 * lightbox -- a dialog portalled over the Overview -- shipping beside it until the last page moved.
 * The last five were the suite surfaces (GEX, Live, Reports, Advisor, Config); the lightbox, its
 * carousel ring and its keyboard wiring went with them. A `Record` over every `ModuleId`, so a page
 * added to `moduleOrder.ts` without a manifest does not compile.
 *
 * Lazy per entry: a session only ever opens one page at a time, so the Overview's first load should
 * not pay for twelve manifests' worth of analytics queries and chart cards.
 */
export const MODULE_FRAMES: Record<ModuleId, ComponentType<{ slide: string }>> = {
  flies: lazy(() => import("./manifests/FliesLightbox").then((m) => ({ default: m.FliesLightbox }))),
  meic: lazy(() => import("./manifests/MeicLightbox").then((m) => ({ default: m.MeicLightbox }))),
  bwb: lazy(() => import("./manifests/BwbLightbox").then((m) => ({ default: m.BwbLightbox }))),
  earnings: lazy(() => import("./manifests/EarningsLightbox").then((m) => ({ default: m.EarningsLightbox }))),
  curve: lazy(() => import("./manifests/CurveLightbox").then((m) => ({ default: m.CurveLightbox }))),
  pmcc: lazy(() => import("./manifests/PmccLightbox").then((m) => ({ default: m.PmccLightbox }))),
  calendars: lazy(() => import("./manifests/CalendarsLightbox").then((m) => ({ default: m.CalendarsLightbox }))),
  gex: lazy(() => import("./manifests/GexLightbox").then((m) => ({ default: m.GexLightbox }))),
  live: lazy(() => import("./manifests/LiveLightbox").then((m) => ({ default: m.LiveLightbox }))),
  reports: lazy(() => import("./manifests/ReportsLightbox").then((m) => ({ default: m.ReportsLightbox }))),
  advisor: lazy(() => import("./manifests/AdvisorLightbox").then((m) => ({ default: m.AdvisorLightbox }))),
  system: lazy(() => import("./manifests/SystemLightbox").then((m) => ({ default: m.SystemLightbox }))),
  config: lazy(() => import("./manifests/ConfigLightbox").then((m) => ({ default: m.ConfigLightbox }))),
};
