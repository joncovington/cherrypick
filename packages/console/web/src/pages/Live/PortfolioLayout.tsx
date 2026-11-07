import type { ReactNode } from "react";
import type { ManageAccount } from "@console/shared";
import { AddingTradesCard } from "./IncomeCard";
import { ContributionCard, DirectionCard, GuidanceCard, IncomeParentCard, RoomCard, StressCard } from "./PortfolioCards";
import { LadderCard, RiskMatrixCard } from "./ScenarioCards";

/**
 * The portfolio tab's layout (2026-11-06, docs: the "portfolio tab layouts" review): the cards grouped
 * under the questions a manager asks, in that order: what needs attention, am I paid enough, which way
 * am I leaning, what if it moves, do I have room, what could I add. Cards in a row share its height
 * and fold to two a row, then one, as the page narrows (container queries on `.pf-layout`). What
 * explains or audits a card opens from it as a lightbox (PortfolioBox.tsx) instead of taking a row.
 */

export type Concept = "stand" | "income" | "dir" | "risk" | "room" | "add";

const NAME: Record<Concept, string> = {
  stand: "where the book stands",
  income: "income",
  dir: "direction",
  risk: "movement risk",
  room: "room to act",
  add: "adding trades",
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

export function PortfolioLayout({ a }: { a: ManageAccount }) {
  const p = a.portfolio;
  if (!p) return null;
  return (
    <div className="pf-layout">
      <Section concept="stand" question="What needs my attention?">
        <GuidanceCard p={p} />
      </Section>
      <Section concept="income" question="Am I being paid enough?">
        <IncomeParentCard p={p} premium={a.premium} />
        <ContributionCard p={p} figure="theta" />
      </Section>
      <Section concept="dir" question="Which way am I leaning?">
        <DirectionCard p={p} />
        <ContributionCard p={p} figure="bwd" />
      </Section>
      <Section concept="risk" question="What happens if it moves?">
        <StressCard p={p} />
        <RiskMatrixCard p={p} />
      </Section>
      <Section concept="room" question="Do I have room to act?">
        <RoomCard p={p} limits={a.limits} />
        <LadderCard ladder={p.ladder} />
      </Section>
      <Section concept="add" question="What could I add?">
        <AddingTradesCard c={p.candidates} t={p.timeSpreads} netLiq={p.netLiq} />
      </Section>
    </div>
  );
}
