import type { ComponentType } from "react";
import { MeicLightbox } from "./manifests/MeicLightbox";
import { FliesLightbox } from "./manifests/FliesLightbox";
import { PmccLightbox } from "./manifests/PmccLightbox";
import { CurveLightbox } from "./manifests/CurveLightbox";
import { BwbLightbox } from "./manifests/BwbLightbox";
import { CalendarsLightbox } from "./manifests/CalendarsLightbox";
import { EarningsLightbox } from "./manifests/EarningsLightbox";
import type { ModuleId } from "./moduleOrder";

export const MODULE_LIGHTBOXES: Record<ModuleId, ComponentType<{ slide: string }>> = {
  meic: MeicLightbox,
  flies: FliesLightbox,
  pmcc: PmccLightbox,
  curve: CurveLightbox,
  bwb: BwbLightbox,
  calendars: CalendarsLightbox,
  earnings: EarningsLightbox,
};
