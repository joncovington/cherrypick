import { lazy, type ComponentType } from "react";
import type { ModuleId } from "./moduleOrder";

/**
 * Lazy per module: a session only ever has one lightbox open at a time, so there is no reason for
 * Overview's initial load to pay for all ten manifests (each pulling in its own analytics
 * queries, chart cards and history tables) up front. `LightboxFrame`'s own portal/inert wiring and
 * every shared component (Card, ScopeBar, the shared chart kit) stay in the main chunk since
 * Overview and every lightbox use them; only the module-specific manifest code-splits.
 */
export const MODULE_LIGHTBOXES: Record<ModuleId, ComponentType<{ slide: string }>> = {
  meic: lazy(() => import("./manifests/MeicLightbox").then((m) => ({ default: m.MeicLightbox }))),
  flies: lazy(() => import("./manifests/FliesLightbox").then((m) => ({ default: m.FliesLightbox }))),
  pmcc: lazy(() => import("./manifests/PmccLightbox").then((m) => ({ default: m.PmccLightbox }))),
  curve: lazy(() => import("./manifests/CurveLightbox").then((m) => ({ default: m.CurveLightbox }))),
  bwb: lazy(() => import("./manifests/BwbLightbox").then((m) => ({ default: m.BwbLightbox }))),
  calendars: lazy(() => import("./manifests/CalendarsLightbox").then((m) => ({ default: m.CalendarsLightbox }))),
  earnings: lazy(() => import("./manifests/EarningsLightbox").then((m) => ({ default: m.EarningsLightbox }))),
  gex: lazy(() => import("./manifests/GexLightbox").then((m) => ({ default: m.GexLightbox }))),
  reports: lazy(() => import("./manifests/ReportsLightbox").then((m) => ({ default: m.ReportsLightbox }))),
  advisor: lazy(() => import("./manifests/AdvisorLightbox").then((m) => ({ default: m.AdvisorLightbox }))),
  config: lazy(() => import("./manifests/ConfigLightbox").then((m) => ({ default: m.ConfigLightbox }))),
};
