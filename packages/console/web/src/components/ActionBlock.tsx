import type { ReactNode } from "react";
import { EvidenceTag } from "./EvidenceTag";

/**
 * What a trade needs (2026-11-06): the rule that fired with its evidence grade, why in plain words, and the
 * proposed order (an order chain, a roll's candidates) under it. It sits at the top of the card of a
 * trade that needs action, which also carries the action as an amber chip in its head and is listed
 * first under its own heading. Advice only: nothing here places an order.
 */
export function ActionBlock({
  rule,
  evidence,
  confidence,
  reason,
  children,
}: {
  rule: string;
  evidence?: string | null;
  confidence?: number | null;
  reason: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="action-block" role="note" aria-label={`Needs action: ${rule}`}>
      <div className="action-block-head">
        <span className="chip chip-warn">{rule}</span>
        <EvidenceTag evidence={evidence} confidence={confidence} />
      </div>
      <p className="action-block-why">{reason}</p>
      {children}
    </div>
  );
}
