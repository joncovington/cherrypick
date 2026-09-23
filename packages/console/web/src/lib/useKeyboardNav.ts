import { useEffect, useRef } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { isModuleId } from "../lightbox/moduleOrder";
import { navSlideIds, resolveSlide } from "../lightbox/navGroups";
import { NAV_DECL } from "../lightbox/navGroups";
import { reduceKey, type KeyState } from "./keyboardNav";

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
 * Escape is deliberately not handled here: the detail sheet owns its own, and so does the
 * lightbox, and a third listener racing them is how a single Escape closes two things.
 */
export function useKeyboardNav() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const stateRef = useRef<KeyState>({ pendingG: null });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const seg = location.pathname.split("/")[1] ?? "";
      const module = isModuleId(seg) ? seg : null;
      const slides = module === null ? [] : navSlideIds(module);
      const rawSlide = location.pathname.split("/")[2] ?? "";
      const slide =
        module === null || slides.length === 0
          ? null
          : resolveSlide(module, rawSlide, NAV_DECL[module]?.slides ?? []);

      const { state, action } = reduceKey(stateRef.current, {
        key: e.key,
        now: Date.now(),
        editable: isEditable(e.target),
        sheetOpen: document.querySelector('.sheet[role="dialog"]') !== null,
        modified: e.ctrlKey || e.metaKey || e.altKey,
        slides,
        slide,
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
  }, [location.pathname, navigate, params]);
}
