import { Suspense } from "react";
import { useParams } from "react-router-dom";
import { NotFoundPage } from "./NotFoundPage";
import { FrameChrome } from "../lightbox/ModuleFrame";
import { MODULE_FRAMES } from "../lightbox/registry";
import { isModuleId } from "../lightbox/moduleOrder";
import { NAV_DECL, resolveSlide, viewSlides } from "../lightbox/navGroups";
import { useFeatures } from "../lib/useFeatures";
import { useViewMode } from "../lib/viewMode";
import { isHiddenAsUnknown, isSlideVisible, offReason } from "../lib/visibility";
import { useLiveLoopsInUse } from "../lib/useLiveToday";
import { ModuleOffCard } from "./ModuleOffCard";

/**
 * `/:module` and `/:module/:slide`: the page body inside the shell's own rail+content grid.
 *
 * Since 2026-10-11 the rail lives in the SHELL (one nav for every page), so this route renders
 * the content pane only — the module context bar (`FrameChrome`), then the manifest's frame. The
 * rail and the breadcrumb still sit OUTSIDE the Suspense boundary deliberately. The manifest is
 * lazy, and React's server renderer emits the fallback rather than resolving it, so anything
 * inside the boundary is invisible to every test this package has. Outside it, the route tests
 * can assert on which tab a URL resolved to — which is most of what there is to get wrong here.
 * It also means a reader sees their place immediately, with only the content arriving late,
 * rather than an empty screen for the length of a chunk fetch.
 */
export function ModuleRoute() {
  const { module = "", slide = "" } = useParams();
  const features = useFeatures();
  const liveInUse = useLiveLoopsInUse(features);
  const view = useViewMode();
  if (!isModuleId(module)) return <NotFoundPage />;

  // A fail-closed page that is not shown (Backtest: licensed vendor data) is no page at all: the
  // 404, not the off card, which would say it exists and why it is off. Checked before anything
  // else, including while the features answer is still loading.
  if (isHiddenAsUnknown(module, features)) return <NotFoundPage />;

  // A page the suite has turned off is a card saying so, never the module and never a 404.
  const off = offReason(module, features);
  if (off !== null) return <ModuleOffCard module={module} off={off} />;

  const Frame = MODULE_FRAMES[module];
  const slides = NAV_DECL[module].slides.filter((s) => isSlideVisible(module, s.id, features, liveInUse));
  const activeId = resolveSlide(module, slide, slides, viewSlides(module, slides, view));
  const label = slides.find((s) => s.id === activeId)?.label ?? activeId;

  return (
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
  );
}
