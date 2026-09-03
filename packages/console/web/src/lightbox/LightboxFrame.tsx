import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useNavigate, useSearchParams } from "react-router-dom";
import { MODULE_LABEL, type ModuleId } from "./moduleOrder";
import { nextSlideId, prevSlideId, firstSlideId, nextModule, prevModule } from "./useCarousel";
import type { SlideDef } from "./types";

/**
 * The shared chrome every module's lightbox renders inside: a portal to `document.body`, a
 * dialog with the module name/badge/loop status, a slide rail, prev/next/auto-advance/close, and
 * the active slide's own content. Each module's own manifest component (`manifests/*.tsx`) owns
 * its data fetching and slide list; this component owns navigation, keyboard, focus and the
 * inert-shell behaviour.
 */
export function LightboxFrame({
  module,
  slide,
  slides,
  badge,
  loopPill,
  session,
  headerControls,
  persistentTop,
}: {
  module: ModuleId;
  slide: string;
  slides: SlideDef[];
  badge?: ReactNode;
  loopPill?: ReactNode;
  session?: string | null;
  headerControls?: ReactNode;
  /** Rendered once, between the rail and the active slide -- for content that must stay visible
   *  across every slide (the module's measurement-integrity strip), rather than being repeated
   *  inside each slide's own render(). */
  persistentTop?: ReactNode;
}) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [autoAdvance, setAutoAdvance] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<Element | null>(null);

  const activeId = slides.some((s) => s.id === slide) ? slide : (firstSlideId(slides) ?? slide);
  const idx = slides.findIndex((s) => s.id === activeId);
  const active = slides[idx];

  const withQs = (path: string) => {
    const qs = params.toString();
    return qs ? `${path}?${qs}` : path;
  };
  const gotoSlide = (id: string) => navigate(withQs(`/${module}/${id}`));
  const gotoModule = (m: ModuleId) => navigate(withQs(`/${m}`));
  const close = () => navigate(withQs("/"));

  const goNext = () => {
    const nid = nextSlideId(slides, activeId);
    if (nid !== null) gotoSlide(nid);
    else gotoModule(nextModule(module));
  };
  const goPrev = () => {
    const pid = prevSlideId(slides, activeId);
    if (pid !== null) gotoSlide(pid);
    else gotoModule(prevModule(module));
  };

  // Redirect a stale/unknown slide id in the URL to the module's real first slide, without
  // leaving a dead entry in history.
  useEffect(() => {
    if (slide !== activeId) navigate(withQs(`/${module}/${activeId}`), { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [module, slide, activeId]);

  useEffect(() => {
    openerRef.current = document.activeElement;
    const shellRoot = document.querySelector(".shell");
    shellRoot?.setAttribute("inert", "");
    shellRoot?.setAttribute("aria-hidden", "true");
    document.body.classList.add("lb-open");
    dialogRef.current?.focus();
    return () => {
      shellRoot?.removeAttribute("inert");
      shellRoot?.removeAttribute("aria-hidden");
      document.body.classList.remove("lb-open");
      (openerRef.current as HTMLElement | null)?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        close();
      } else if (e.key === "ArrowRight") {
        goNext();
      } else if (e.key === "ArrowLeft") {
        goPrev();
      } else if (e.key === "Home") {
        const fid = firstSlideId(slides);
        if (fid !== undefined) gotoSlide(fid);
      } else if (e.key === "End") {
        const lid = slides.length > 0 ? slides[slides.length - 1]!.id : undefined;
        if (lid !== undefined) gotoSlide(lid);
      } else if (e.key === " " && e.target === dialogRef.current) {
        e.preventDefault();
        setAutoAdvance((v) => !v);
      } else if (e.key === "Tab") {
        const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
        );
        if (focusable === undefined || focusable.length === 0) return;
        const first = focusable[0]!;
        const last = focusable[focusable.length - 1]!;
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [module, activeId, slides.length]);

  useEffect(() => {
    if (!autoAdvance) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const id = window.setInterval(goNext, 8000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoAdvance, module, activeId]);

  // No `document` outside a browser (server-render tests included) -- a portal has nowhere to
  // mount, and every effect above is a no-op there too. The module page underneath (rendered by
  // the caller) still carries real content for a route/SSR check to assert on.
  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      className="lb-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        className="lb-frame"
        role="dialog"
        aria-modal="true"
        aria-label={`${MODULE_LABEL[module]} — ${active?.label ?? ""}`}
        ref={dialogRef}
        tabIndex={-1}
      >
        <div className="lb-head">
          <h1>{MODULE_LABEL[module]}</h1>
          {badge}
          {loopPill}
          {session != null && <span className="muted">session {session}</span>}
          {headerControls}
          <div className="lb-nav">
            <button type="button" className="lb-arrow" aria-label="previous slide" onClick={goPrev}>
              ‹
            </button>
            <span className="mono lb-count">
              {slides.length > 0 ? idx + 1 : 0} / {slides.length} · {active?.label}
            </span>
            <button type="button" className="lb-arrow" aria-label="next slide" onClick={goNext}>
              ›
            </button>
            <button
              type="button"
              className={`lb-arrow ${autoAdvance ? "active" : ""}`}
              aria-label={autoAdvance ? "pause auto-advance" : "start auto-advance"}
              aria-pressed={autoAdvance}
              onClick={() => setAutoAdvance((v) => !v)}
            >
              {autoAdvance ? "❚❚" : "▶"}
            </button>
            <button type="button" className="lb-arrow" aria-label="close" onClick={close}>
              ✕
            </button>
          </div>
        </div>
        <div className="lb-rail">
          {slides.map((s) => (
            <button
              key={s.id}
              type="button"
              className={`lb-rail-chip ${s.id === activeId ? "active" : ""}`}
              title={s.available === false ? s.unavailableReason : undefined}
              disabled={s.available === false}
              onClick={() => gotoSlide(s.id)}
            >
              {s.label}
            </button>
          ))}
        </div>
        {persistentTop}
        <div className="lb-body" key={activeId}>
          {active?.render()}
        </div>
      </div>
    </div>,
    document.body,
  );
}
