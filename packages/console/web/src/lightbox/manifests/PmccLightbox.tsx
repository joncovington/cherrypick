import { useState } from "react";
import { usePmcc } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard, fmtPct } from "../../components/DataTable";
import { EraSelect, LoopPill, ScopeSelect } from "../../components/ScopeBar";
import { ArmRail, AttemptTimeline } from "../../components/Attempts";
import { IntegrityStrip } from "../../pages/Pmcc/IntegrityStrip";
import { BookComparison, OpenTradesCard } from "../../pages/Pmcc/CurrentStateCards";
import { DecisionsCard } from "../../components/DecisionsCard";
import { HistoryTab } from "../../pages/Pmcc/HistoryTab";
import { TrackerTab } from "../../pages/Pmcc/TrackerTab";
import { WeeklyByArmCard } from "../../pages/Pmcc/WeeklyByArmCard";
import { HelpTab } from "../../pages/Pmcc/HelpTab";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { AdvisorSlide } from "../../components/advisor/AdvisorSlide";
import { ModuleFrame } from "../ModuleFrame";
import { PMCC_SLIDES, type PmccSlideId } from "../navGroups";
import { PmccSession } from "../../pages/Pmcc/PmccSession";
import type { SlideDef } from "../types";

const PMCC_LABEL = Object.fromEntries(PMCC_SLIDES.map((s) => [s.id, s.label])) as Record<PmccSlideId, string>;

/**
 * PMCC, on the module frame since 2026-09-25: a left rail of pages, and nothing on the surface
 * opens an overlay. No mode toggle -- structural: the module has no live loop and no live store.
 */
export function PmccLightbox({ slide }: { slide: string }) {
  const [symbol, setSymbol] = useState<string | null>(null);
  const [era, setEra] = useState<string | null>(null);
  const { data, isLoading, isError, dataUpdatedAt } = usePmcc(era);
  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";

  const slides: Array<SlideDef & { id: PmccSlideId }> = [
    { id: "session", label: PMCC_LABEL.session, render: () => <PmccSession data={data} loading={isLoading} symbol={symbol} /> },
    {
      id: "decisions",
      label: PMCC_LABEL.decisions,
      render: () => (
        <div className="cards cards-wide">
          <ArmRail module="pmcc" mode="paper" date={null} />
          <AttemptTimeline module="pmcc" mode="paper" date={null} />
          <div className="pmcc-activity">
            {(() => {
              const attempts = data?.today.attempts.filter((a) => symbol === null || a.symbol === symbol) ?? [];
              return (
                <DataCard
                  title="entry attempts today"
                  headers={["symbol", "arm", "outcome", "n", "detail"]}
                  loading={isLoading}
                  isError={isError}
                  rowCount={attempts.length}
                  numFrom={3}
                  empty="no entry opportunities evaluated on this session"
                  updatedAt={dataUpdatedAt}
                >
                  {attempts.map((a) => (
                    <tr key={`${a.symbol}-${a.arm}-${a.outcome}`}>
                      <td>{a.symbol}</td>
                      <td>{a.arm}</td>
                      <td>{a.outcome}</td>
                      <td>{a.n}</td>
                      <td className="muted">
                        {a.blockDetail ?? "—"}
                        {a.bestYield !== null && (
                          <span title="the best weekly yield the chain actually offered"> · best {fmtPct(a.bestYield * 100, 2)}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </DataCard>
              );
            })()}
            {(() => {
              const events = data?.today.events.filter((e) => symbol === null || e.symbol === null || e.symbol === symbol) ?? [];
              return (
                <DataCard
                  title="management events today"
                  headers={["symbol", "action", "reason", "n", ""]}
                  loading={isLoading}
                  isError={isError}
                  rowCount={events.length}
                  numFrom={3}
                  empty="no management verdicts recorded on this session"
                  updatedAt={dataUpdatedAt}
                >
                  {events.map((e) => (
                    <tr key={`${e.symbol ?? "—"}-${e.action}-${e.reason}-${String(e.executed)}-${e.gate ?? ""}`}>
                      <td>{e.symbol ?? <span className="muted">—</span>}</td>
                      <td>{e.action}</td>
                      <td className="mono">{e.reason}</td>
                      <td>{e.n}</td>
                      <td>
                        {!e.executed && (
                          <span
                            className="chip chip-warn integrity-chip"
                            title="The verdict was reached but an execution gate held it. The record that an exit was SEEN before it was allowed."
                          >
                            seen, held{e.gate === null ? "" : ` by ${e.gate}`}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </DataCard>
              );
            })()}
          </div>
          <DecisionsCard module="pmcc" />
        </div>
      ),
    },
    {
      id: "arms",
      label: PMCC_LABEL.arms,
      render: () => (
        <div className="cards cards-wide">
          <BookComparison data={data} updatedAt={dataUpdatedAt} symbol={symbol} />
          <WeeklyByArmCard symbol={symbol} era={era} />
        </div>
      ),
    },
    { id: "performance", label: PMCC_LABEL.performance, render: () => <PerformanceSlide module="pmcc" /> },
    { id: "advisor", label: PMCC_LABEL.advisor, render: () => <AdvisorSlide module="pmcc" /> },
    {
      id: "positions",
      label: PMCC_LABEL.positions,
      render: () => (
        <div className="cards cards-wide">
          {isLoading ? (
            <DataCard title="open trades" headers={["symbol", "arm", "opened"]} loading rowCount={0} numFrom={3}>
              {null}
            </DataCard>
          ) : (
            <OpenTradesCard data={data} updatedAt={dataUpdatedAt} symbol={symbol} />
          )}
        </div>
      ),
    },
    { id: "tracker", label: PMCC_LABEL.tracker, render: () => <TrackerTab symbol={symbol} /> },
    { id: "history", label: PMCC_LABEL.history, render: () => <HistoryTab /> },
    { id: "guide", label: PMCC_LABEL.guide, render: () => <HelpTab data={data} /> },
  ];

  return (
    <ModuleFrame
      module="pmcc"
      slide={slide}
      slides={slides}
      badge={<PaperLiveBadge mode="paper" />}
      headerControls={
        <>
          <ScopeSelect label="symbol filter" value={symbol} options={data?.params.symbols} onChange={setSymbol} allLabel="all symbols" />
          <EraSelect
            value={era}
            eras={data?.eras}
            currentEra={data?.currentEra}
            onChange={setEra}
            title="The arm comparison and the weekly A/B are scoped to this era. The shield era (2026-10-05) added the held-long arms and changed the symbols; pooling it with the redesign era reads as one book when it is really two."
            pooledLabel="every era, pooled"
          />
        </>
      }
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
