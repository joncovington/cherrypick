import type { CurvePayload } from "@console/shared";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { MarkedEquitySection } from "../../components/nav/MarkedEquitySection";
import { useCurveEquity } from "../../lib/api";
import { Section } from "../../components/Section";

/**
 * curve's performance page. It leads with the daily MARKED equity (`curve equity`): a credit spread
 * can sit deep under water for weeks and close a small win, which the closed-trade view below
 * records as the win and nothing else, so that view comes second.
 */
export function CurvePerformance({ data }: { data: CurvePayload | undefined }) {
  const equity = useCurveEquity();
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="marked" question="What is the book worth each day, with open spreads at their marks?">
      <MarkedEquitySection
        equity={equity.data}
        loading={equity.isLoading}
        regimeRows={(data?.regimeSeries ?? []).filter((r) => r.usable)}
        collapsePrefix="curve-perf"
        note="Dollars from zero, after every cost: each closed cycle's net on its close session, each open spread at that session's last usable mark less its entry costs. A session with no usable mark carries the last one and is counted."
      />
      </Section>
      <PerformanceSlide module="curve" />
    </div>
  );
}
