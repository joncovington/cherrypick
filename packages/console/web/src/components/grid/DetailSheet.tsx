import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

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
 *
 * **Portalled to `document.body`, and it disables everything BESIDE itself, never above it.** Until
 * 2026-09-24 it rendered inline and set `inert` on `closest(".mf")` -- the module frame it was
 * rendered inside -- so it disabled itself: every sheet drew normally and ignored every click,
 * scroll and Tab, and only Escape (listened for on `document`) still worked. Now it sits beside the
 * page, marks each OTHER top-level element inert (the shell, and a lightbox if one is open), and
 * only then takes focus, so focus lands inside it and the Tab loop can hold it. The rule for any
 * overlay here: never make an ancestor of the dialog inert. `pnpm ui-check --route <r> --sheets`
 * checks it in a real browser; a static render cannot see `inert` or focus.
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
  const backdropRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<Element | null>(null);
  const headingId = useId();

  useEffect(() => {
    if (!open || typeof document === "undefined") return;
    openerRef.current = document.activeElement;
    // Nothing behind the sheet may take a click, a scroll or focus -- two scrollbars, one of which
    // moves content the reader cannot see, is the usual way a modal loses people. Only elements
    // this sheet disables are restored; one already inert (the shell under an open lightbox)
    // belongs to whoever set it.
    const host = backdropRef.current;
    const disabled: Element[] = [];
    for (const el of Array.from(document.body.children)) {
      if (host === null || el === host || el.contains(host) || el.hasAttribute("inert")) continue;
      el.setAttribute("inert", "");
      el.setAttribute("aria-hidden", "true");
      disabled.push(el);
    }
    document.body.classList.add("sheet-open");
    dialogRef.current?.focus();
    return () => {
      for (const el of disabled) {
        el.removeAttribute("inert");
        el.removeAttribute("aria-hidden");
      }
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

  const sheet = (
    <div
      className="sheet-backdrop"
      ref={backdropRef}
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
  // The server renderer has no document and no portals; it renders the sheet in place, which is
  // all a static render (and the tests built on one) can check anyway.
  return typeof document === "undefined" ? sheet : createPortal(sheet, document.body);
}
