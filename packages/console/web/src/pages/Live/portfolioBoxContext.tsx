import { createContext, useContext, type ReactNode } from "react";

/**
 * How a portfolio card opens one of the tab's lightboxes (PortfolioBox.tsx) without importing it. The
 * default opens nothing and has nothing, so a card rendered on its own (a test) draws no buttons.
 */
export const PortfolioBoxContext = createContext<{ open: (id: string) => void; has: (id: string) => boolean }>({
  open: () => undefined,
  has: () => false,
});

export function usePortfolioBox() {
  return useContext(PortfolioBoxContext);
}

/** The button a card opens a lightbox with; nothing when the account has no such lightbox. */
export function BoxButton({ id, children, hot = false }: { id: string; children: ReactNode; hot?: boolean }) {
  const box = usePortfolioBox();
  if (!box.has(id)) return null;
  return (
    <button type="button" className={`pf-more${hot ? " is-hot" : ""}`} onClick={() => box.open(id)}>
      {children} <span aria-hidden="true">›</span>
    </button>
  );
}

/** A row of lightbox buttons at the card's foot. */
export function BoxButtons({ children }: { children: ReactNode }) {
  return <div className="pf-mores pf-push">{children}</div>;
}

export function expiryBoxId(expiration: string): string {
  return `expiry-${expiration}`;
}
