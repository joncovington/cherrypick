import { useEffect, useRef } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { isModuleId } from "../lightbox/moduleOrder";
import { navSlideIds, resolveSlide } from "../lightbox/navGroups";
import { NAV_DECL } from "../lightbox/navGroups";
import { reduceKey, paletteKeyFor, type KeyState } from "./keyboardNav";
import { useFeatures } from "./useFeatures";
import { isSlideVisible } from "./visibility";
import { setPaletteOpen } from "../components/shell/paletteStore";

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
 * Mounted once, in the Shell. Listens, asks `reduceKey` what the key means, and navigates — or
 * opens the command palette (`Ctrl/Cmd+K`, or a bare `?` outside a text field; the decision lives
 * in `paletteKeyFor`, not here).
 *
 * The palette OPEN action closes over `setPaletteOpen`, a store rather than state, because the
 * hook mounts once and the palette renders wherever the shell puts it.
 *
 * Escape is deliberately not handled here: the detail sheet and the palette own their own, and a
 * second listener racing them is how a single Escape closes two things.
 */
export function useKeyboardNav() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const stateRef = useRef<KeyState>({ pendingG: null });
  const features = useFeatures();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const seg = location.pathname.split("/")[1] ?? "";
      const module = isModuleId(seg) ? seg : null;
      const slides = module === null ? [] : navSlideIds(module).filter((id) => isSlideVisible(module, id, features));
      const rawSlide = location.pathname.split("/")[2] ?? "";
      const slide =
        module === null || slides.length === 0
          ? null
          : resolveSlide(module, rawSlide, NAV_DECL[module]?.slides ?? []);

      const { state, action } = reduceKey(stateRef.current, {
        key: e.key,
        now: Date.now(),
        editable: isEditable(e.target),
        modified: e.ctrlKey || e.metaKey || e.altKey,
        slides,
        slide,
        features,
        paletteKey: paletteKeyFor(e),
      });
      stateRef.current = state;
      if (action === null) return;

      e.preventDefault();
      if (action.kind === "palette") {
        setPaletteOpen(true);
        return;
      }
      const qs = params.toString();
      const withQs = (p: string) => (qs ? `${p}?${qs}` : p);
      if (action.kind === "navigate") navigate(action.to);
      else navigate(withQs(`/${String(module)}/${action.id}`));
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [location.pathname, navigate, params, features]);
}
