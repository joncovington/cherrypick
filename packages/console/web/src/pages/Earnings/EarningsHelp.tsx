import { Card } from "../../components/DataTable";
import { GuideLink } from "../../components/GuideLink";
import { Section } from "../../components/Section";

/** Earnings' help tab: the rule in words and how to read the pages, with the guide one click away. */
export function EarningsHelp() {
  return (
    <div className="pf-layout">
      <GuideLink to="/help/strategy-pages#earnings-earnings" />
      <Section concept="stand" tag="the rule" question="What does this module do?">
        <Card title="the earnings trades" collapseKey="earnings-help-rule">
          <div className="pmcc-prose">
            <p>
              Defined-risk trades around earnings announcements: each session the scan screens the names reporting soon, and each strategy (an iron
              fly, an iron condor, a directional credit spread, a broken-wing butterfly, a calendar) places its own structure on the names that pass.
              Paper only: a position is a ledger row, never an order.
            </p>
            <p>An arm is one strategy under one profile, so the strategies are read side by side on the same names and the same sessions.</p>
          </div>
        </Card>
      </Section>
      <Section concept="edge" tag="reading it" question="What do the pages show, and what do they not?">
        <Card title="what is a verdict and what is context" collapseKey="earnings-help-reading">
          <div className="pmcc-prose">
            <ol className="pmcc-rules">
              <li>P&amp;L is net: gross less fees, settlement and slippage, each its own column on the history, costs and performance tabs.</li>
              <li>
                <b>screening</b> shows which names passed and why the rest did not; <b>strategies</b> compares the structures against each other, net
                of costs.
              </li>
              <li>
                <b>performance</b> reads each arm per trade (its expectancy, win rate, drawdown and spread), and <b>changes</b> shows each advisor
                experiment against the arm it shadows.
              </li>
              <li>An announcement is a few sessions a quarter per name, so a strategy's sample grows slowly: judge it over many quarters, never one.</li>
            </ol>
          </div>
        </Card>
      </Section>
    </div>
  );
}
