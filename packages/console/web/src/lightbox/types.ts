import type { ReactNode } from "react";

export interface SlideDef {
  id: string;
  label: string;
  render: () => ReactNode;
  /** Default true. A slide that is not yet built (a later phase) or has nothing to show for this
   *  module renders greyed in the rail with `unavailableReason` as its title, and is skipped by
   *  keyboard/arrow stepping. */
  available?: boolean;
  unavailableReason?: string;
}
