import { Suspense } from "react";
import { useParams } from "react-router-dom";
import { OverviewPage } from "./OverviewPage";
import { NotFoundPage } from "../NotFoundPage";
import { MODULE_LIGHTBOXES } from "../../lightbox/registry";
import { isModuleId } from "../../lightbox/moduleOrder";

/**
 * `/:module` and `/:module/:slide` both land here: the Overview stays mounted underneath as the
 * carousel's backdrop (still polling, inert while the dialog is open -- `LightboxFrame` handles
 * the inert/focus-trap wiring), and the requested module's carousel renders over it. An unknown
 * module name is a real 404, not a layered dialog over a page that has nothing to do with it.
 */
export function OverviewWithLightbox() {
  const { module = "", slide = "" } = useParams();
  if (!isModuleId(module)) return <NotFoundPage />;
  const Lightbox = MODULE_LIGHTBOXES[module];
  return (
    <>
      <OverviewPage />
      {/* No spinner fallback -- the chunk itself renders LightboxFrame's backdrop/frame, so there
          is nothing to show a loading state INSIDE until it arrives; Overview stays visible and
          interactive underneath for the (typically sub-100ms, same-origin) gap. */}
      <Suspense fallback={null}>
        <Lightbox slide={slide} />
      </Suspense>
    </>
  );
}
