import { useEffect, useRef } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { isModuleId } from "../lightbox/moduleOrder";
import { NAV_DECL, resolveSlide, viewSlides } from "../lightbox/navGroups";
import { readViewMode } from "./viewMode";
import { reduceKey, type KeyState } from "./keyboardNav";
import { useFeatures } from "./useFeatures";
import { isSlideVisible } from "./visibility";
import { useLiveLoopsInUse } from "./useLiveToday";

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return (
    tag === "INPUT" ||
    tag === "SELECT" ||
    tag === "TEXTAREA" ||
    target.isContentEditable
  );
}

/**
 * Mounted once, in the Shell. Listens, asks `reduceKey` what the key means, and navigates.
 *
 * Escape is deliberately not handled here: the detail sheet and the header menu own their own,
 * and a second listener racing them is how a single Escape closes two things.
 */
export function useKeyboardNav() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const stateRef = useRef<KeyState>({ pendingG: null });
  const features = useFeatures();
  const liveInUse = useLiveLoopsInUse(features);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const seg = location.pathname.split("/")[1] ?? "";
      const module = isModuleId(seg) ? seg : null;
      const rawSlide = location.pathname.split("/")[2] ?? "";
      const visible = module === null ? [] : (NAV_DECL[module]?.slides ?? []).filter((s) => isSlideVisible(module, s.id, features, liveInUse));
      const view = readViewMode();
      const slide =
        module === null || visible.length === 0
          ? null
          : resolveSlide(module, rawSlide, visible, viewSlides(module, visible, view));
      // j/k step through the tabs the rail lists in this view, the current one always included.
      const slides = module === null ? [] : viewSlides(module, visible, view, slide).map((s) => s.id);

      const { state, action } = reduceKey(stateRef.current, {
        key: e.key,
        now: Date.now(),
        editable: isEditable(e.target),
        modified: e.ctrlKey || e.metaKey || e.altKey,
        slides,
        slide,
        features,
      });
      stateRef.current = state;
      if (action === null) return;

      e.preventDefault();
      const qs = params.toString();
      const withQs = (p: string) => (qs ? `${p}?${qs}` : p);
      if (action.kind === "navigate") navigate(action.to);
      else navigate(withQs(`/${String(module)}/${action.id}`));
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [location.pathname, navigate, params, features, liveInUse]);
}
