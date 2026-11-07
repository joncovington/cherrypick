import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode, type TouchEvent } from "react";
import { createPortal } from "react-dom";
import { useNavigate, useSearchParams } from "react-router-dom";

/**
 * The Backtest page's lightbox (2026-11-03): every tab's cards open one. A fixed head (kicker, title,
 * chips, close, tabs), a scrolling body, and a foot that steps to the previous or next card. Esc
 * closes, ← → step, a swipe steps or (down, from the top) closes. Full screen on a phone.
 *
 * `LightboxFrame` is the markup alone, so a test can render it; `CardLightbox` portals it to the body
 * and adds the focus, scroll lock and keys. `useOpenParam` keeps the open card in the URL, so a link
 * opens it and the back button closes it. Class names keep the studies tab's `study-dialog` prefix,
 * where they started; the styles are in styles.css.
 */

export interface LightboxTab<T extends string = string> {
  id: T;
  label: string;
  /** A small count or word beside the label ("2 flagged"). */
  badge?: string | null;
}

function IconButton({ label, onClick, path }: { label: string; onClick: () => void; path: string }) {
  return (
    <button type="button" className="study-icon-btn" aria-label={label} title={label} onClick={onClick}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <path d={path} />
      </svg>
    </button>
  );
}

export function LightboxFrame<T extends string>({
  kicker,
  title,
  chips,
  tabs,
  tab,
  onTab,
  position,
  onPrev,
  onNext,
  onClose,
  children,
}: {
  kicker: ReactNode;
  title: ReactNode;
  chips?: ReactNode;
  tabs: LightboxTab<T>[];
  tab: T;
  onTab: (t: T) => void;
  position: string;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <>
      <header className="study-dialog-head">
        <div className="study-dialog-top">
          <div className="study-dialog-titles">
            <div className="study-dialog-kicker">{kicker}</div>
            <h2 id="study-dialog-title" className="study-dialog-title">
              {title}
            </h2>
            {chips ? <div className="study-dialog-chips">{chips}</div> : null}
          </div>
          <IconButton label="Close" onClick={onClose} path="M6 6l12 12M18 6L6 18" />
        </div>
        {tabs.length > 1 ? (
          <div className="study-tabs" role="tablist" aria-label="Detail">
            {tabs.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                id={`study-tab-${t.id}`}
                aria-selected={tab === t.id}
                aria-controls="study-tabpanel"
                className={tab === t.id ? "study-tab is-on" : "study-tab"}
                onClick={() => onTab(t.id)}
              >
                {t.label}
                {t.badge ? <span className="chip chip-warn study-tab-badge">{t.badge}</span> : null}
              </button>
            ))}
          </div>
        ) : (
          <div className="study-tabs" />
        )}
      </header>

      <div className="study-dialog-body" id="study-tabpanel" role="tabpanel" aria-labelledby={`study-tab-${tab}`}>
        {children}
      </div>

      <footer className="study-dialog-foot">
        <button type="button" className="study-step-btn" onClick={onPrev}>
          Previous
        </button>
        <span>
          {position}
          <span className="study-keyhint"> · Esc closes · ← → step</span>
        </span>
        <button type="button" className="study-step-btn" onClick={onNext}>
          Next
        </button>
      </footer>
    </>
  );
}

/** The portal, focus, scroll lock, keys and swipe around a frame; `children` gets the navigation. */
export function CardLightbox({
  count,
  index,
  onStep,
  onClose,
  children,
  wide = false,
}: {
  count: number;
  index: number;
  onStep: (to: number) => void;
  onClose: () => void;
  children: (nav: { prev: () => void; next: () => void; position: string }) => ReactNode;
  /** The portfolio tab's wider panel, for trade cards side by side. */
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const touch = useRef<{ x: number; y: number } | null>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current?.focus({ preventScroll: true });
    return () => {
      document.body.style.overflow = overflow;
      before?.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => {
    panel.current?.querySelector(".study-dialog-body")?.scrollTo({ top: 0 });
  }, [index]);
  const step = (d: number) => onStep((index + d + count) % count);
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      step(1);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      step(-1);
    }
  };
  const onTouchStart = (e: TouchEvent<HTMLDivElement>) => {
    const t = e.touches[0];
    touch.current = t ? { x: t.clientX, y: t.clientY } : null;
  };
  const onTouchEnd = (e: TouchEvent<HTMLDivElement>) => {
    const start = touch.current;
    const t = e.changedTouches[0];
    touch.current = null;
    if (!start || !t) return;
    const dx = t.clientX - start.x;
    const dy = t.clientY - start.y;
    const body = panel.current?.querySelector(".study-dialog-body");
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx < 0 ? 1 : -1);
    else if (dy > 90 && Math.abs(dy) > Math.abs(dx) * 1.5 && (body?.scrollTop ?? 0) <= 0) onClose();
  };
  return createPortal(
    <div className="study-dialog-wrap">
      <button type="button" className="study-dialog-backdrop" aria-label="Close" tabIndex={-1} onClick={onClose} />
      <div
        ref={panel}
        className={wide ? "study-dialog study-dialog-wide" : "study-dialog"}
        role="dialog"
        aria-modal="true"
        aria-labelledby="study-dialog-title"
        tabIndex={-1}
        onKeyDown={onKeyDown}
        onTouchStart={onTouchStart}
        onTouchEnd={onTouchEnd}
      >
        {children({ prev: () => step(-1), next: () => step(1), position: `${String(index + 1)} of ${String(count)}` })}
      </div>
    </div>,
    document.body,
  );
}

/**
 * The open card in the URL (`?<param>=<id>`). Opening from a card pushes, so closing goes back and
 * the browser's back button agrees; stepping replaces; a card opened from a link closes in place.
 */
export function useOpenParam(param: string): { open: string | null; show: (id: string) => void; step: (id: string) => void; close: () => void } {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [pushed, setPushed] = useState(false);
  const set = (id: string | null, push: boolean) => {
    const next = new URLSearchParams(params);
    if (id === null) next.delete(param);
    else next.set(param, id);
    setParams(next, { replace: !push });
  };
  return {
    open: params.get(param),
    show: (id) => {
      setPushed(true);
      set(id, true);
    },
    step: (id) => set(id, false),
    close: () => {
      if (pushed) {
        setPushed(false);
        navigate(-1);
      } else set(null, false);
    },
  };
}
