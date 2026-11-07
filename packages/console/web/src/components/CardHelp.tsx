import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * A card's help (2026-11-06, the suite's card-help pattern): the card's explanation (where its figures
 * come from, how they are made, what was left out, the caps) sits behind a small ? beside its title and
 * opens in a small lightbox, so the card shows its reading and not its method. Anything that changes
 * what a person would do stays on the card: an error, a stale reading, a red line. A caveat that must
 * not be missed keeps a small amber flag beside the ? (`CardFlag`), which opens the same box.
 *
 * The box is about 480 px wide and sized to its text; on a phone it rises as a sheet from the bottom.
 * Esc, the close button or a click outside closes it, and focus goes back to the ?.
 */

export interface CardHelpContent {
  /** The box's heading, e.g. "Where these trades come from". */
  title: string;
  /** Short sections: an <h4> and a paragraph or list each. A `HelpWarn` section leads when there is one. */
  body: ReactNode;
}

/** A warning section inside the box: an amber rule, its heading in amber. */
export function HelpWarn({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="help-warn">
      <h4>{title}</h4>
      {children}
    </div>
  );
}

/** One plain section inside the box. */
export function HelpSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="help-sec">
      <h4>{title}</h4>
      {children}
    </div>
  );
}

function HelpBox({ cardTitle, help, onClose, labelId }: { cardTitle: string; help: CardHelpContent; onClose: () => void; labelId: string }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    box.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      before?.focus({ preventScroll: true });
    };
  }, [onClose]);
  return createPortal(
    <div className="help-wrap">
      <button type="button" className="help-scrim" aria-label="Close" tabIndex={-1} onClick={onClose} />
      <div ref={box} className="help-box" role="dialog" aria-modal="true" aria-labelledby={labelId} tabIndex={-1}>
        <div className="help-head">
          <div>
            <div className="help-kick">{cardTitle} · about this card</div>
            <h3 id={labelId} className="help-title">
              {help.title}
            </h3>
          </div>
          <button type="button" className="help-x" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="help-body">{help.body}</div>
        <div className="help-foot">Esc closes</div>
      </div>
    </div>,
    document.body,
  );
}

/** The ? beside a card's title, and the amber flag when the card has one; both open the box. */
export function CardHelpButton({ cardTitle, help, flag }: { cardTitle: string; help: CardHelpContent; flag?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const labelId = useId();
  const close = () => setOpen(false);
  return (
    <>
      <button
        type="button"
        className={`help-q${open ? " is-open" : ""}`}
        aria-label={`About ${cardTitle}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={`About ${cardTitle}`}
        onClick={() => setOpen(true)}
      >
        ?
      </button>
      {flag ? (
        <button type="button" className="help-flag" onClick={() => setOpen(true)} title="Open the ? for the full text">
          {flag}
        </button>
      ) : null}
      {open ? <HelpBox cardTitle={cardTitle} help={help} onClose={close} labelId={labelId} /> : null}
    </>
  );
}
