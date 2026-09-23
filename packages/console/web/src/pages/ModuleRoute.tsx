import { Suspense } from "react";
import { useParams } from "react-router-dom";
import { NotFoundPage } from "./NotFoundPage";
import { OverviewWithLightbox } from "./Overview/OverviewWithLightbox";
import { ModuleNav } from "../components/shell/ModuleNav";
import { FrameChrome } from "../lightbox/ModuleFrame";
import { MODULE_FRAMES, isFrameModule } from "../lightbox/registry";
import { isModuleId } from "../lightbox/moduleOrder";
import { NAV_DECL, resolveSlide } from "../lightbox/navGroups";

/**
 * `/:module` and `/:module/:slide` for both shapes of module page.
 *
 * A module still on the lightbox goes to `OverviewWithLightbox` exactly as before — the Overview
 * underneath, the dialog portalled over it. A module on the frame renders here instead, inside
 * the shell's own outlet, as a rail plus a content pane.
 *
 * The rail and the breadcrumb sit OUTSIDE the Suspense boundary deliberately. The manifest is
 * lazy, and React's server renderer emits the fallback rather than resolving it, so anything
 * inside the boundary is invisible to every test this package has. Outside it, the route tests
 * can assert on the nav and on which tab a URL resolved to — which is most of what there is to
 * get wrong here. It also means a reader sees the frame and their place in it immediately, with
 * only the content arriving late, rather than an empty screen for the length of a chunk fetch.
 */
export function ModuleRoute() {
  const { module = "", slide = "" } = useParams();
  if (!isModuleId(module)) return <NotFoundPage />;
  if (!isFrameModule(module)) return <OverviewWithLightbox />;

  const Frame = MODULE_FRAMES[module];
  const slides = NAV_DECL[module]?.slides ?? [];
  const activeId = resolveSlide(module, slide, slides);
  const label = slides.find((s) => s.id === activeId)?.label ?? activeId;

  return (
    <div className="mf">
      <ModuleNav module={module} slide={activeId} />
      <Suspense
        fallback={
          <div className="mf-layout">
            <FrameChrome module={module} slideLabel={label} />
            <div className="mf-body" />
          </div>
        }
      >
        <Frame slide={slide} />
      </Suspense>
    </div>
  );
}
