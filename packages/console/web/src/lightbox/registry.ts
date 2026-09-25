import { lazy, type ComponentType } from "react";
import type { ModuleId } from "./moduleOrder";

/**
 * Which modules have moved to the module frame, and which are still lightboxes.
 *
 * The suite is being converted one module at a time rather than in one landing, so for a while
 * both shapes ship: a frame module renders a left rail and a content pane inside the shell, a
 * lightbox module still portals a dialog over the Overview. `ModuleRoute` reads this to decide
 * which, and nothing else needs to know.
 *
 * `MODULE_LIGHTBOXES` is narrowed to `LightboxModuleId` on purpose. It would be easy to leave it
 * covering every module and let a converted one sit in both maps, and the page would even work —
 * whichever branch ran first would win. Narrowing makes moving a module a compile error until it
 * is removed from the other side, so the two maps cannot both claim it.
 *
 * Lazy per entry, as before: a session only ever opens one module at a time, so the Overview's
 * first load should not pay for twelve manifests' worth of analytics queries and chart cards.
 */
export const FRAME_MODULE_IDS = ["flies", "meic", "bwb"] as const;

export type FrameModuleId = (typeof FRAME_MODULE_IDS)[number];
export type LightboxModuleId = Exclude<ModuleId, FrameModuleId>;

export function isFrameModule(m: ModuleId): m is FrameModuleId {
  return (FRAME_MODULE_IDS as readonly string[]).includes(m);
}

export const MODULE_FRAMES: Record<FrameModuleId, ComponentType<{ slide: string }>> = {
  flies: lazy(() => import("./manifests/FliesLightbox").then((m) => ({ default: m.FliesLightbox }))),
  meic: lazy(() => import("./manifests/MeicLightbox").then((m) => ({ default: m.MeicLightbox }))),
  bwb: lazy(() => import("./manifests/BwbLightbox").then((m) => ({ default: m.BwbLightbox }))),
};

export const MODULE_LIGHTBOXES: Record<LightboxModuleId, ComponentType<{ slide: string }>> = {
  pmcc: lazy(() => import("./manifests/PmccLightbox").then((m) => ({ default: m.PmccLightbox }))),
  curve: lazy(() => import("./manifests/CurveLightbox").then((m) => ({ default: m.CurveLightbox }))),
  calendars: lazy(() => import("./manifests/CalendarsLightbox").then((m) => ({ default: m.CalendarsLightbox }))),
  earnings: lazy(() => import("./manifests/EarningsLightbox").then((m) => ({ default: m.EarningsLightbox }))),
  gex: lazy(() => import("./manifests/GexLightbox").then((m) => ({ default: m.GexLightbox }))),
  live: lazy(() => import("./manifests/LiveLightbox").then((m) => ({ default: m.LiveLightbox }))),
  reports: lazy(() => import("./manifests/ReportsLightbox").then((m) => ({ default: m.ReportsLightbox }))),
  advisor: lazy(() => import("./manifests/AdvisorLightbox").then((m) => ({ default: m.AdvisorLightbox }))),
  config: lazy(() => import("./manifests/ConfigLightbox").then((m) => ({ default: m.ConfigLightbox }))),
};
