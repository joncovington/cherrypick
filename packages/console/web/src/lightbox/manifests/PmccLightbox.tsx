import { useState } from "react";
import { usePmcc } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { Card, DataCard, fmtPct } from "../../components/DataTable";
import { LoopPill, ScopeSelect } from "../../components/ScopeBar";
import { ArmRail, AttemptTimeline } from "../../components/Attempts";
import { IntegrityStrip } from "../../pages/Pmcc/IntegrityStrip";
import { BookComparison, OpenTradesCard } from "../../pages/Pmcc/CurrentStateCards";
import { HistoryTab } from "../../pages/Pmcc/HistoryTab";
import { HelpTab } from "../../pages/Pmcc/HelpTab";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { LightboxFrame } from "../LightboxFrame";
import type { SlideDef } from "../types";

/** PMCC-99. No mode toggle -- structural: the module has no live loop and no live store. */
export function PmccLightbox({ slide }: { slide: string }) {
  const [symbol, setSymbol] = useState<string | null>(null);
  const { data, isLoading, isError, dataUpdatedAt } = usePmcc();
  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";

  const slides: SlideDef[] = [
    {
      id: "now",
      label: "now",
      render: () =>
        data !== undefined && !data.dbPresent ? (
          <div className="cards cards-wide">
            <Card title="PMCC-99" collapseKey="pmcc-absent">
              <p className="muted">
                This module has not run on this machine — there is no paper store at{" "}
                <span className="mono">~/.cherrypick/data/pmcc/paper_trades.db</span> yet. Nothing
                is wrong; the page fills in after its first session.
              </p>
            </Card>
          </div>
        ) : (
          <div className="cards cards-wide">
            {isLoading ? (
              <DataCard
                title="open positions"
                headers={["book", "long", "short", "time value", "spot", "weekly yield", "entry spread", "protection", "assignment"]}
                loading
                rowCount={0}
                numFrom={3}
              >
                {null}
              </DataCard>
            ) : (
              <OpenTradesCard data={data} updatedAt={dataUpdatedAt} symbol={symbol} />
            )}
            <ArmRail module="pmcc" mode="paper" date={null} />
            <AttemptTimeline module="pmcc" mode="paper" date={null} />
            <div className="pmcc-activity">
              {(() => {
                const attempts = data?.today.attempts.filter((a) => symbol === null || a.symbol === symbol) ?? [];
                return (
                  <DataCard
                    title="entry attempts today"
                    headers={["symbol", "book", "outcome", "n", "detail"]}
                    loading={isLoading}
                    isError={isError}
                    rowCount={attempts.length}
                    numFrom={3}
                    empty="no entry opportunities evaluated on this session"
                    updatedAt={dataUpdatedAt}
                  >
                    {attempts.map((a) => (
                      <tr key={`${a.symbol}-${a.book}-${a.outcome}`}>
                        <td>{a.symbol}</td>
                        <td>{a.book}</td>
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
            <BookComparison data={data} updatedAt={dataUpdatedAt} symbol={symbol} />
          </div>
        ),
    },
    { id: "history", label: "history", render: () => <HistoryTab /> },
    { id: "performance", label: "performance", render: () => <PerformanceSlide module="pmcc" /> },
    { id: "guide", label: "help", render: () => <HelpTab data={data} /> },
  ];

  return (
    <LightboxFrame
      module="pmcc"
      slide={slide}
      slides={slides}
      badge={<PaperLiveBadge mode="paper" />}
      headerControls={
        <ScopeSelect label="symbol filter" value={symbol} options={data?.params.symbols} onChange={setSymbol} allLabel="all symbols" />
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
