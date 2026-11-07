import type { ReactNode } from "react";

/**
 * A screener tab's cards under the question they answer (2026-11-06), in the portfolio tab's own
 * layout: a tag naming the concept, the question, then a 12-column row of cards that folds to one
 * column as the page narrows. It reuses the `.pf-*` classes of Live > portfolio.
 */

export type Concept = "stand" | "dir" | "hist";

export function ScreenerLayout({ children }: { children: ReactNode }) {
  return <div className="pf-layout">{children}</div>;
}

export function QuestionSection({ concept, name, question, children }: { concept: Concept; name: string; question: string; children: ReactNode }) {
  return (
    <section className={`pf-sec pf-c-${concept}`} aria-label={name}>
      <div className="pf-sec-head">
        <span className="pf-tag">{name}</span>
        <h3>{question}</h3>
      </div>
      <div className="pf-row">{children}</div>
    </section>
  );
}
