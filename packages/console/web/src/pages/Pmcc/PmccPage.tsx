import { useState } from "react";
import { usePmcc } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { Card, DataCard, fmtPct } from "../../components/DataTable";
import { LoopPill, TabStrip } from "../../components/ScopeBar";
import { IntegrityStrip } from "./IntegrityStrip";
import { BookComparison, KeltnerCard, SymbolCards } from "./CurrentStateCards";
import { HistoryTab } from "./HistoryTab";
import { HelpTab } from "./HelpTab";

type PmccTab = "today" | "history" | "help";

const TABS = ["today", "history", "help"] as const;

/**
 * PMCC-99.
 *
 * No mode toggle, and that is structural: the module has no live loop and no live store, so there
 * is no second book a toggle could reach. Its `live.enabled` config field is a documented
 * placeholder, and offering a switch over it would advertise a capability that does not exist.
 *
 * The page leads with current state — what is open, how much time value is left in each short, and
 * what the entry gate is doing — because that is the question a covered-call book raises daily. The
 * measurement-integrity strip sits above all of it, ahead of any P&L, for the reason the module's
 * own honesty rules give: the paper net is an upper bound, and a caveat met after the number has
 * already been read is a caveat that arrived too late.
 */
export function PmccPage() {
  const [tab, setTab] = useState<PmccTab>("today");
  const { data, isLoading, isError, dataUpdatedAt } = usePmcc();

  const loopState =
    data?.today.lastIteration == null ? "no-data" : data.today.lastIteration.ageSeconds < 900 ? "live" : "idle";

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>PMCC-99</h1>
        <PaperLiveBadge mode="paper" />
        <TabStrip tabs={TABS} value={tab} onChange={setTab} ariaLabel="pmcc tabs" />
        <LoopPill
          state={data === undefined ? undefined : loopState}
          ageSeconds={data?.today.lastIteration?.ageSeconds ?? null}
          detail={
            data?.today.lastIteration == null
              ? "no loop iterations recorded"
              : `${data.today.lastIteration.phase} · ${data.today.lastIteration.status}`
          }
        />
        {data?.session != null && <span className="muted">session {data.session}</span>}
      </div>

      {data !== undefined && !data.dbPresent ? (
        <div className="cards cards-wide">
          <Card title="PMCC-99" collapseKey="pmcc-absent">
            <p className="muted">
              This module has not run on this machine — there is no paper store at{" "}
              <span className="mono">~/.cherrypick/data/pmcc/paper_trades.db</span> yet. Nothing is wrong; the page
              fills in after its first session.
            </p>
          </Card>
        </div>
      ) : (
        <>
          {tab === "today" && (
            <div className="cards cards-wide">
              <IntegrityStrip data={data} updatedAt={dataUpdatedAt} />

              {isLoading ? (
                <DataCard
                  title="open positions"
                  headers={["book", "long", "short", "time value", "spot", "weekly yield", "protection", "assignment"]}
                  loading
                  rowCount={0}
                  numFrom={3}
                >
                  {null}
                </DataCard>
              ) : (
                <SymbolCards data={data} keltner={data?.keltner ?? []} updatedAt={dataUpdatedAt} />
              )}

              <KeltnerCard
                series={data?.keltner ?? []}
                readiness={data?.integrity.keltner ?? []}
                updatedAt={dataUpdatedAt}
              />

              <div className="pmcc-activity">
                <DataCard
                  title="entry attempts today"
                  headers={["book", "outcome", "n", "detail"]}
                  loading={isLoading}
                  isError={isError}
                  rowCount={data?.today.attempts.length ?? 0}
                  numFrom={2}
                  empty="no entry opportunities evaluated on this session"
                  updatedAt={dataUpdatedAt}
                >
                  {data?.today.attempts.map((a) => (
                    <tr key={`${a.book}-${a.outcome}`}>
                      <td>{a.book}</td>
                      <td>{a.outcome}</td>
                      <td>{a.n}</td>
                      <td className="muted">
                        {a.blockDetail ?? "—"}
                        {a.bestYield !== null && (
                          <span title="the best weekly yield the chain actually offered">
                            {" "}
                            · best {fmtPct(a.bestYield * 100, 2)}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </DataCard>

                <DataCard
                  title="management events today"
                  headers={["action", "reason", "n", ""]}
                  loading={isLoading}
                  isError={isError}
                  rowCount={data?.today.events.length ?? 0}
                  numFrom={2}
                  empty="no management verdicts recorded on this session"
                  updatedAt={dataUpdatedAt}
                >
                  {data?.today.events.map((e) => (
                    <tr key={`${e.action}-${e.reason}-${String(e.executed)}-${e.gate ?? ""}`}>
                      <td>{e.action}</td>
                      <td className="mono">{e.reason}</td>
                      <td>{e.n}</td>
                      <td>
                        {!e.executed && (
                          <span
                            className="chip chip-warn pmcc-chip"
                            title="The verdict was reached but an execution gate held it. The record that an exit was SEEN before it was allowed."
                          >
                            seen, held{e.gate === null ? "" : ` by ${e.gate}`}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </DataCard>
              </div>

              <BookComparison data={data} updatedAt={dataUpdatedAt} />
            </div>
          )}

          {tab === "history" && <HistoryTab />}
          {tab === "help" && <HelpTab data={data} />}
        </>
      )}
    </div>
  );
}
