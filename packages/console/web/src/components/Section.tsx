import type { ReactNode } from "react";

/**
 * A page's cards under the question they answer (2026-11-06 redesign): a tag naming the concept in its
 * colour, the question, then a row of cards that folds as the page narrows inside `.pf-layout`. Every
 * redesigned page uses this one, so the sections read alike across the suite.
 */

export type Concept = "stand" | "income" | "dir" | "risk" | "room" | "add" | "live" | "hist" | "edge";

const NAME: Record<Concept, string> = {
  stand: "where the book stands",
  income: "income",
  dir: "direction",
  risk: "movement risk",
  room: "room to act",
  add: "adding trades",
  live: "live",
  hist: "record",
  edge: "edge",
};

/** `tag` names the section in the colour of `concept`, where another tab's own words fit better. */
export function Section({ concept, question, tag, children }: { concept: Concept; question: string; tag?: string; children: ReactNode }) {
  return (
    <section className={`pf-sec pf-c-${concept}`} aria-label={tag ?? NAME[concept]}>
      <div className="pf-sec-head">
        <span className="pf-tag">{tag ?? NAME[concept]}</span>
        <h3>{question}</h3>
      </div>
      <div className="pf-row">{children}</div>
    </section>
  );
}
