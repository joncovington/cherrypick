import { useBwb } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { LoopPill } from "../../components/ScopeBar";
import { IntegrityStrip } from "../../pages/Bwb/IntegrityStrip";
import { Link } from "react-router-dom";
import { FireCountsCard, OpenTradesCard } from "../../pages/Bwb/CurrentStateCards";
import { BwbSession } from "../../pages/Bwb/BwbSession";
import { DecisionsCard } from "../../components/DecisionsCard";
import { Card } from "../../components/DataTable";
import { HelpSection } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { HistoryTab } from "../../pages/Bwb/HistoryTab";
import { HelpTab } from "../../pages/Bwb/HelpTab";
import { BwbLiveGapSlide } from "../../pages/Bwb/LiveGapSlide";
import { BwbVolSlide } from "../../pages/Bwb/VolSlide";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { AnalysisSlide } from "../../components/analysis/AnalysisSlide";
import { AdvisorSlide } from "../../components/advisor/AdvisorSlide";
import { ModuleFrame } from "../ModuleFrame";
import { BWB_SLIDES, type BwbSlideId } from "../navGroups";
import type { SlideDef } from "../types";
import { ModuleSummary } from "../../components/summary/ModuleSummary";
import { BookArmGuide } from "../../components/ExperimentGuide";

const BWB_LABEL = Object.fromEntries(BWB_SLIDES.map((s) => [s.id, s.label])) as Record<BwbSlideId, string>;

/**
 * bwb (SPX daily-laddered put broken-wing butterfly / 1-3-2 add-on trigger experiment), on the
 * module frame since 2026-09-25: a left rail of pages, and nothing on the surface opens an overlay.
 *
 * Paper by default, with the live pilot's book behind the page's paper/live toggle: every slide
 * that reads a ledger follows it. The advisor slide stays paper -- advised books exist only there.
 */
export function BwbLightbox({ slide }: { slide: string }) {
  const [mode, setMode] = useMode();
  const { data, isLoading, dataUpdatedAt } = useBwb(mode);
  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";

  const slides: Array<SlideDef & { id: BwbSlideId }> = [
    { id: "summary", label: BWB_LABEL.summary, render: () => <ModuleSummary module="bwb" mode={mode} today={<BwbSession data={data} loading={isLoading} mode={mode} />} /> },
    {
      id: "decisions",
      label: BWB_LABEL.decisions,
      render: () => (
        <div className="pf-layout">
          <Section concept="live" tag="today" question="Why did each arm enter, or not?">
            <DecisionsCard module="bwb" mode={mode} />
          </Section>
        </div>
      ),
    },
    {
      id: "arms",
      label: BWB_LABEL.arms,
      render: () => (
        <>
          <div className="pf-layout">
            <Section concept="add" tag="the add-on" question="Which arms have fired?">
              {/* Net by arm left this tab 2026-10-30: the summary's by-arm card and Compare both answer
                  it, Compare with each arm paired against control. */}
              <Card
                title="net by arm"
                collapseKey="bwb-arms-where"
                help={{
                  title: "Net by arm",
                  body: (
                    <HelpSection title="An expected collision">
                      <p>
                        Until an arm&rsquo;s add-on fires, its positions are the same as control&rsquo;s, an expected collision rather
                        than a defect: read the fires first.
                      </p>
                    </HelpSection>
                  ),
                }}
              >
                <p className="muted" style={{ margin: 0 }}>
                  Net by arm is on the <Link to="/bwb/summary">summary</Link>, and each arm against control, with paired
                  statistics, is on <Link to="/compare/arms">Compare</Link>.
                </p>
              </Card>
              <FireCountsCard
                counts={data?.fireCounts ?? []}
                correlationCaveat={
                  data?.correlationCaveat ?? "concurrent positions share regime context -- rows are not independent samples"
                }
                updatedAt={dataUpdatedAt}
              />
            </Section>
            <BookArmGuide module="bwb" />
          </div>
        </>
      ),
    },
    { id: "vol", label: BWB_LABEL.vol, render: () => <BwbVolSlide /> },
    { id: "livegap", label: BWB_LABEL.livegap, render: () => <BwbLiveGapSlide /> },
    { id: "performance", label: BWB_LABEL.performance, render: () => <PerformanceSlide module="bwb" mode={mode} /> },
    { id: "analysis", label: BWB_LABEL.analysis, render: () => <AnalysisSlide module="bwb" mode={mode} /> },
    { id: "advisor", label: BWB_LABEL.advisor, render: () => <AdvisorSlide module="bwb" /> },
    {
      id: "positions",
      label: BWB_LABEL.positions,
      render: () => (
        <div className="pf-layout">
          <Section concept="stand" tag="open" question="What is open?">
            {isLoading ? null : <OpenTradesCard data={data} updatedAt={dataUpdatedAt} />}
          </Section>
        </div>
      ),
    },
    { id: "history", label: BWB_LABEL.history, render: () => <HistoryTab mode={mode} /> },
    { id: "guide", label: BWB_LABEL.guide, render: () => <HelpTab data={data} /> },
  ];

  return (
    <ModuleFrame
      module="bwb"
      slide={slide}
      slides={slides}
      badge={<PaperLiveBadge mode={mode} />}
      headerControls={<ModeToggle mode={mode} onChange={setMode} />}
      loopPill={
        <LoopPill
          state={data === undefined ? undefined : loopState}
          ageSeconds={data?.today.lastIteration?.ageSeconds ?? null}
          detail={
            data?.today.lastIteration == null
              ? "no loop iterations recorded"
              : `${data.today.lastIteration.phase} · ${data.today.lastIteration.status}`
          }
        />
      }
      session={data?.session ?? null}
      integrity={<IntegrityStrip data={data} updatedAt={dataUpdatedAt} />}
      integrityAttention={(data?.integrity.measurementBreaks.length ?? 0) > 0 || (data?.integrity.schemaDrift.length ?? 0) > 0}
    />
  );
}
