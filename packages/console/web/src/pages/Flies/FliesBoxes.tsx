import type { ReactNode } from "react";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "../Backtest/CardLightbox";

/**
 * The flies evidence tabs' lightboxes (2026-11-07): each card's boxes named in the URL, so a link opens
 * one, the back button closes it, and the arrows step through the card's boxes. The tabs' sections and
 * layout are FliesFilter's.
 */

/** One lightbox a card opens: `kicker` names the card it came from, `tabs` are its views. */
export interface FliesBox {
  id: string;
  kicker: string;
  title: ReactNode;
  chips?: ReactNode;
  tabs: LightboxTab[];
  body: (tab: string) => ReactNode;
}

export type OpenParam = ReturnType<typeof useOpenParam>;

/** The URL value that opens box `id` at `tab`: `id`, or `id:tab` for a tab other than the first. */
export function boxValue(id: string, tab?: string): string {
  return tab === undefined ? id : `${id}:${tab}`;
}

/** Which box a URL value names, and at which of its tabs: an unknown tab opens the first, an unknown box nothing. */
export function resolveBox(open: string | null, boxes: FliesBox[]): { box: FliesBox; index: number; tab: string } | null {
  if (open === null) return null;
  const index = boxes.findIndex((b) => open === b.id || open.startsWith(`${b.id}:`));
  const box = index >= 0 ? boxes[index] : undefined;
  if (box === undefined) return null;
  const asked = open.slice(box.id.length + 1);
  return { box, index, tab: box.tabs.find((t) => t.id === asked)?.id ?? box.tabs[0]?.id ?? "" };
}

/**
 * The open lightbox of one card, named in the URL (`?<param>=<id>` or `?<param>=<id>:<tab>`), so a link
 * opens it, the back button closes it, and the arrows step through the card's boxes. `lb` is the
 * card's `useOpenParam`, which it also opens boxes with.
 */
export function FliesBoxHost({ lb, boxes }: { lb: OpenParam; boxes: FliesBox[] }) {
  const hit = resolveBox(lb.open, boxes);
  if (hit === null) return null;
  const { box, index, tab } = hit;
  return (
    <CardLightbox count={boxes.length} index={index} onStep={(to) => lb.step(boxes[to]?.id ?? "")} onClose={lb.close} wide>
      {(nav) => (
        <LightboxFrame
          kicker={box.kicker}
          title={box.title}
          chips={box.chips}
          tabs={box.tabs}
          tab={tab}
          onTab={(t) => lb.step(boxValue(box.id, t))}
          position={nav.position}
          onPrev={nav.prev}
          onNext={nav.next}
          onClose={lb.close}
        >
          <div className="pf-box-body">{box.body(tab)}</div>
        </LightboxFrame>
      )}
    </CardLightbox>
  );
}

/** A button at a card's foot that opens one of its lightboxes. */
export function BoxOpen({ lb, id, tab, children, hot = false }: { lb: OpenParam; id: string; tab?: string; children: ReactNode; hot?: boolean }) {
  return (
    <button type="button" className={`pf-more${hot ? " is-hot" : ""}`} onClick={() => lb.show(boxValue(id, tab))}>
      {children} <span aria-hidden="true">›</span>
    </button>
  );
}
