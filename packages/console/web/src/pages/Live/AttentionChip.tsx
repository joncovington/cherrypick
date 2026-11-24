import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { LiveTodayPayload } from "@console/shared";

type Item = LiveTodayPayload["attention"][number];

/** An attention item's kind in words: `held_submission` reads "held submission". */
export const kindWords = (kind: string) => kind.replace(/_/g, " ");

/**
 * The loops card's "needs attention" chip. At zero it is a plain label. With items it is a button that
 * opens their list in place, over what is below: each is the loop, the condition and the loop's own
 * sentence (`attentionFor` in the server's liveToday reader, in the order it gave them; nothing is ranked
 * here), and a link to where it is. A loop with a card on the page scrolls to that card (`onGo`); one
 * without (an off module) links to its own page. Escape or a click outside closes the list.
 * `initiallyOpen` only seeds the state, for a test that renders on the server.
 */
export function AttentionChip({
  items,
  hasCard,
  onGo,
  initiallyOpen = false,
}: {
  items: Item[];
  hasCard: (loop: string) => boolean;
  onGo: (loop: string) => void;
  initiallyOpen?: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const box = useRef<HTMLSpanElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const listId = useId();
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        toggle.current?.focus(); // a keyboard user is not dropped at the top of the page
      }
    };
    const onDown = (e: MouseEvent) => {
      if (box.current !== null && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  if (items.length === 0) return <span className="chip">needs attention · 0</span>;
  return (
    <span className="att-wrap" ref={box}>
      <button ref={toggle} type="button" className="chip chip-bad att-chip" aria-expanded={open} aria-controls={listId} onClick={() => setOpen((o) => !o)}>
        needs attention · {items.length} <span aria-hidden="true">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div id={listId} className="att-pop" role="region" aria-label="what needs attention">
          <div className="att-pop-head">
            {items.length} item{items.length === 1 ? "" : "s"}
          </div>
          <ul>
            {items.map((a, i) => (
              <li key={`${a.loop}-${a.kind}-${String(i)}`} className="att-item">
                <span className="att-loop">{a.loop}</span>
                <div>
                  <div className="att-kind">{kindWords(a.kind)}</div>
                  <div className="att-detail">{a.detail}</div>
                  {hasCard(a.loop) ? (
                    <button
                      type="button"
                      className="att-go"
                      onClick={() => {
                        setOpen(false);
                        onGo(a.loop);
                      }}
                    >
                      Go to {a.loop} →
                    </button>
                  ) : (
                    <Link to={a.to} className="att-go" onClick={() => setOpen(false)}>
                      Go to {a.loop} →
                    </Link>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </span>
  );
}
