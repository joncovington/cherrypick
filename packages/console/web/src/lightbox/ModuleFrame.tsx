import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { MODULE_LABEL, type ModuleId } from "./moduleOrder";
import { navSlideIds, resolveSlide } from "./navGroups";
import type { SlideDef } from "./types";

/**
 * What every page renders inside: the breadcrumb header, the integrity drawer, and the body.
 *
 * It took over the old `LightboxFrame`'s prop contract unchanged, so each page moved onto the frame
 * with one import line (2026-09-22 to 2026-09-25); the lightbox was removed once the last had moved.
 *
 * What it keeps: slide resolution and the replace-navigate for an id the module does not have,
 * the query string riding along on every tab change, and the body staying mounted while only the
 * slide's own subtree is keyed. That last one is the 2026-09 rule and it matters more here than
 * it did there — the frame is the page now, so remounting the body would blank the whole viewport.
 *
 * What it drops: the portal, the backdrop, `inert` on the shell, the focus trap, auto-advance,
 * and the arrow keys that wrapped into the next module. Navigation is the rail; the ring is gone.
 * No animation of its own — motion in the console is being designed as its own piece of work, and
 * the frame should start from nothing rather than from a habit.
 */
export function FrameChrome({
  module,
  slideLabel,
  badge,
  loopPill,
  session,
  headerControls,
  trailing,
}: {
  module: ModuleId;
  slideLabel: string;
  badge?: ReactNode;
  loopPill?: ReactNode;
  session?: string | null;
  headerControls?: ReactNode;
  trailing?: ReactNode;
}) {
  return (
    <div className="mf-sub">
      {/* The separator carries its own spaces rather than leaning on a flex gap, so the
          breadcrumb reads "Flies / forest" in `innerText` too. `pnpm ui-check --expect` matches
          against the rendered text, and a gap is invisible to it. */}
      <span className="mf-crumb">
        <strong>{MODULE_LABEL[module]}</strong>
        <span className="mf-crumb-sep">{" / "}</span>
        <span className="mf-crumb-slide">{slideLabel}</span>
      </span>
      {badge}
      {loopPill}
      {session != null && <span className="muted">session {session}</span>}
      <div className="mf-controls">
        {headerControls}
        {trailing}
      </div>
    </div>
  );
}

export function ModuleFrame({
  module,
  slide,
  slides,
  badge,
  loopPill,
  session,
  headerControls,
  persistentTop,
  integrity,
  integrityAttention,
}: {
  module: ModuleId;
  slide: string;
  slides: SlideDef[];
  badge?: ReactNode;
  loopPill?: ReactNode;
  session?: string | null;
  headerControls?: ReactNode;
  persistentTop?: ReactNode;
  integrity?: ReactNode;
  integrityAttention?: boolean;
}) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [integrityOpen, setIntegrityOpen] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  const activeId = resolveSlide(module, slide, slides);
  const active = slides.find((s) => s.id === activeId);

  const withQs = (path: string) => {
    const qs = params.toString();
    return qs ? `${path}?${qs}` : path;
  };

  // The body is reused across tabs, so nothing resets the scroll for us any more.
  useEffect(() => {
    if (bodyRef.current !== null) bodyRef.current.scrollTop = 0;
  }, [activeId]);

  // A drawer left open across a tab change sits over content it has nothing to say about.
  useEffect(() => {
    setIntegrityOpen(false);
  }, [activeId]);

  // An id the module does not have — a stale bookmark, or one of the renamed tabs — is rewritten
  // to what it resolved to, without leaving a dead entry in history.
  useEffect(() => {
    if (slide !== activeId) navigate(withQs(`/${module}/${activeId}`), { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [module, slide, activeId]);

  // The rail is built from static data (`navGroups.ts`) and the body from the manifest's own
  // list. If they disagree the rail simply omits a tab, which reads as "that tab was removed"
  // rather than as a defect — so say it out loud instead.
  const declared = navSlideIds(module);
  const undeclared = declared.length === 0 ? [] : slides.filter((s) => !declared.includes(s.id));

  return (
    <div className="mf-layout">
      <FrameChrome
        module={module}
        slideLabel={active?.label ?? activeId}
        badge={badge}
        loopPill={loopPill}
        session={session}
        headerControls={headerControls}
        trailing={
          integrity !== undefined && (
            <button
              type="button"
              className={`mf-integrity-chip ${integrityAttention === true ? "chip-warn" : ""} ${integrityOpen ? "active" : ""}`}
              aria-expanded={integrityOpen}
              onClick={() => setIntegrityOpen((v) => !v)}
            >
              measurement integrity {integrityOpen ? "▾" : "▸"}
            </button>
          )
        }
      />
      {undeclared.length > 0 && (
        <div className="mf-warn">
          {undeclared.length} tab{undeclared.length === 1 ? "" : "s"} the nav does not know about, so
          {undeclared.length === 1 ? " it is" : " they are"} unreachable from the rail:{" "}
          {undeclared.map((s) => s.id).join(", ")}
        </div>
      )}
      {integrityOpen && integrity !== undefined && (
        <div className="mf-integrity-drawer">{integrity}</div>
      )}
      {persistentTop}
      <div className="mf-body" ref={bodyRef}>
        <div className="mf-slide" key={activeId}>
          {active?.render()}
        </div>
      </div>
    </div>
  );
}
