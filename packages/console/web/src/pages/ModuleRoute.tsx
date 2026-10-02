import { Suspense } from "react";
import { useParams } from "react-router-dom";
import { NotFoundPage } from "./NotFoundPage";
import { ModuleNav, RAIL_PREF } from "../components/shell/ModuleNav";
import { useBoolPref } from "../lib/prefs";
import { FrameChrome } from "../lightbox/ModuleFrame";
import { MODULE_FRAMES } from "../lightbox/registry";
import { isModuleId } from "../lightbox/moduleOrder";
import { NAV_DECL, resolveSlide } from "../lightbox/navGroups";
import { useFeatures } from "../lib/useFeatures";
import { isSlideVisible, offReason } from "../lib/visibility";
import { ModuleOffCard } from "./ModuleOffCard";

/**
 * `/:module` and `/:module/:slide`: every page renders here, inside the shell's own outlet, as a
 * rail plus a content pane.
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
  const railExpanded = useBoolPref(RAIL_PREF);
  const features = useFeatures();
  if (!isModuleId(module)) return <NotFoundPage />;

  // A page the suite has turned off is a card saying so, never the module and never a 404.
  const off = offReason(module, features);
  if (off !== null) {
    return (
      <div className={railExpanded ? "mf" : "mf mf-rail-collapsed"}>
        <ModuleNav module={module} slide="" />
        <ModuleOffCard module={module} off={off} />
      </div>
    );
  }

  const Frame = MODULE_FRAMES[module];
  const slides = NAV_DECL[module].slides.filter((s) => isSlideVisible(module, s.id, features));
  const activeId = resolveSlide(module, slide, slides);
  const label = slides.find((s) => s.id === activeId)?.label ?? activeId;

  return (
    <div className={railExpanded ? "mf" : "mf mf-rail-collapsed"}>
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
