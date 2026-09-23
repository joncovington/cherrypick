import { useEffect, useId, useRef, type ReactNode } from "react";

/**
 * Where a dense table lives, now that the frame holds visualizations.
 *
 * Opened from a card's ⤢ and scoped to that card's subject: the forest opens its books, "net by
 * arm" opens the positions behind those bars. Shneiderman's details-on-demand, and the reason it
 * is a sheet rather than a tab is that the table is a zoom on the tab you are already reading,
 * not a place of its own.
 *
 * **Deliberately not a route.** A reload returns to the tab, not to the table. Cockburn's survey
 * of overview+detail interfaces makes the point that a zoom costs the reader their place, so it
 * has to be cheap to reverse — Escape, the backdrop, or the close button, and focus goes back to
 * the ⤢ that opened it. Putting it in the URL would make the cheap thing expensive: a back button
 * that leaves the module rather than the table.
 *
 * No animation: motion in this console is being designed as its own piece of work, and the frame
 * starts from none rather than from a habit.
 */
export function DetailSheet({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<Element | null>(null);
  const headingId = useId();

  useEffect(() => {
    if (!open || typeof document === "undefined") return;
    openerRef.current = document.activeElement;
    dialogRef.current?.focus();
    // The frame's own body must not scroll behind the sheet -- two scrollbars, one of which moves
    // content the reader cannot see, is the usual way a modal loses people.
    const frame = dialogRef.current?.closest(".mf") ?? null;
    frame?.setAttribute("inert", "");
    document.body.classList.add("sheet-open");
    return () => {
      frame?.removeAttribute("inert");
      document.body.classList.remove("sheet-open");
      (openerRef.current as HTMLElement | null)?.focus?.();
    };
  }, [open]);

  useEffect(() => {
    if (!open || typeof document === "undefined") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])',
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
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="sheet-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        ref={dialogRef}
        tabIndex={-1}
      >
        <div className="sheet-head">
          <h2 id={headingId}>{title}</h2>
          <button type="button" className="sheet-close" aria-label="close detail" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  );
}
