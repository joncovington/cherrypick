import { Suspense } from "react";
import { useParams } from "react-router-dom";
import { OverviewPage } from "./OverviewPage";
import { NotFoundPage } from "../NotFoundPage";
import { MODULE_LIGHTBOXES, isFrameModule } from "../../lightbox/registry";
import { isModuleId } from "../../lightbox/moduleOrder";

/**
 * The lightbox shape of a module page: the Overview stays mounted underneath as the carousel's
 * backdrop (still polling, inert while the dialog is open -- `LightboxFrame` handles the
 * inert/focus-trap wiring), and the requested module's carousel renders over it. An unknown
 * module name is a real 404, not a layered dialog over a page that has nothing to do with it.
 *
 * Reached through `ModuleRoute`, which sends the modules that have moved to the frame elsewhere.
 * The `isFrameModule` guard below is how `MODULE_LIGHTBOXES` gets its narrowed key type: it is
 * unreachable in practice, and that is the point -- a module in both maps would not compile.
 */
export function OverviewWithLightbox() {
  const { module = "", slide = "" } = useParams();
  if (!isModuleId(module) || isFrameModule(module)) return <NotFoundPage />;
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
