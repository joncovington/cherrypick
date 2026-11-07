import { useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { BacktestOverview, BacktestRunListing, BacktestRunReport, StudyResult, StudyRow } from "@console/shared";
import { HelpSection } from "../../components/CardHelp";
import { Card } from "../../components/DataTable";
import { ListViewContext, ViewSwitch, useListView, useViewChoice } from "../../components/ViewSwitch";
import { BtRows, type BtRow } from "./BtRows";
import { fmtCash, fmtCount } from "../../lib/format";
import { ArmsTable, LicenceFlag, PairingTable, ProvisionalChip, RefusedSessions, RunCardsSection, RunPlainEnglish, WithOverview, etStamp, measure, span, trialsLine, useRun, useRunChoices } from "./BacktestPage";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "./CardLightbox";
import { ago, etMs, etNowMs } from "./QueueTab";
import { HealthChecks, MonthGrid, settingsLine } from "./RunCards";
import { EquityLines, controlScale, fmtTrades, tradesPerSession } from "./StudyCards";
import { indexStudies, splitTitle, studyHref, useStudies, verdictChip, type StudyIndex } from "./studyIndex";

/**
 * The Backtest page's runs tab as cards (2026-11-03): one tile per replay, newest first and the
 * newest large, each naming the study whose result file names it and that study's verdict. A tile
 * opens a lightbox on `?run=` (the link the other tabs already use): the run in plain words with
 * each arm's interval against control, each arm's running net, its health checks, its month grid,
 * and its settings. The full report (2026-11-06) is folded into the same lightbox as more tabs: arms,
 * paired with control, run cards (the account picker, sizing check, compounded lens, drawdowns and the
 * trade log) and refused sessions, so a run never leaves the board. An old `?run=&full=1` link opens the
 * lightbox on the run cards.
 *
 * Every figure is the report's and every verdict the study's: the page draws them. An interval is
 * drawn untoned (interval ends are never toned); a net figure is toned by its sign.
 */

type StudyHit = { row: StudyRow; result: StudyResult };

/** Newest first, filtered to one module or none. */
export function orderedRuns(runs: BacktestRunListing[], module: string | null): BacktestRunListing[] {
  return [...runs].filter((r) => module === null || r.module === module).sort((a, b) => b.created_at - a.created_at || b.run_id.localeCompare(a.run_id));
}

/** Each module with a run, in the order runs first appear newest first. */
export function runModules(runs: BacktestRunListing[]): string[] {
  return [...new Set(orderedRuns(runs, null).map((r) => r.module))];
}

/** "2026-10-28 06:50" → its age on the ET scale the queue uses. */
function runAge(createdAt: number, nowMs: number): string {
  return ago(etMs(etStamp(createdAt).replace(" ", "T")), nowMs);
}

function VerdictLine({ hit }: { hit: StudyHit | undefined }) {
  if (!hit) return <span className="study-tile-sub">No study in the register names this run.</span>;
  return (
    <span className="r-study">
      <span className={verdictChip(hit.result.summary)}>{hit.result.summary ?? "no verdict word"}</span>
      <span className="study-tile-slug">{hit.row.study}</span>
    </span>
  );
}

export function RunTile({ r, hit, hero, nowMs, onOpen }: { r: BacktestRunListing; hit?: StudyHit; hero: boolean; nowMs: number; onOpen: () => void }) {
  const arms = r.arms.filter((a) => a !== "control");
  return (
    <button type="button" className={hero ? "study-tile study-hero r-tile" : "study-tile r-tile"} data-run={r.run_id} aria-label={`Open run ${r.run_id}`} onClick={onOpen}>
      <span className="r-chips">
        <span className="chip muted">{r.module}</span>
        <span className="chip">{r.window}</span>
        {r.provisional === true ? <span className="chip chip-warn">provisional</span> : null}
        <span className="study-tile-sub r-age">{runAge(r.created_at, nowMs)}</span>
      </span>
      <span className="study-tile-mid">
        {hero ? <span className="study-tile-sub">Newest run</span> : null}
        <span className={hero ? "study-hero-title" : "study-tile-title"}>{hit ? splitTitle(hit.row.title)[0] : r.run_id}</span>
        <VerdictLine hit={hit} />
        {hero && arms.length > 0 ? (
          <span className="r-arms">
            {arms.map((a) => (
              <span key={a} className="chip muted r-arm">
                {a}
              </span>
            ))}
          </span>
        ) : null}
      </span>
      <span className="study-tile-foot">
        <span className="r-span">{span(r.span)}</span>
        <span className="study-tile-sub">
          {fmtCount(r.sessions)} sessions · {arms.length} arm{arms.length === 1 ? "" : "s"} · {r.cadence} s
        </span>
      </span>
    </button>
  );
}

/** A run's tile on one line: module and window, the study's title, its verdict and span, its size and age. */
export function runRow(r: BacktestRunListing, hit: StudyHit | undefined, nowMs: number, onOpen: () => void): BtRow {
  const arms = r.arms.filter((a) => a !== "control");
  return {
    key: r.run_id,
    label: `Open run ${r.run_id}`,
    lead: (
      <>
        <span className="chip muted">{r.module}</span>
        <span className="chip">{r.window}</span>
        {r.provisional === true ? <span className="chip chip-warn">provisional</span> : null}
      </>
    ),
    title: hit ? splitTitle(hit.row.title)[0] : r.run_id,
    meta: (
      <>
        {hit ? <span className={verdictChip(hit.result.summary)}>{hit.result.summary ?? "no verdict word"}</span> : "no study"} {span(r.span)}
      </>
    ),
    end: `${fmtCount(r.sessions)} sessions · ${String(arms.length)} arm${arms.length === 1 ? "" : "s"} · ${runAge(r.created_at, nowMs)}`,
    data: { "data-run": r.run_id },
    onOpen,
  };
}

// ------------------------------------------------------------------ the lightbox

export type RunTab = "summary" | "equity" | "health" | "calendar" | "settings" | "arms" | "paired" | "cards" | "refused";

/** Where a value sits on a symmetric scale around zero, as a percentage of the bar's width. */
function at(v: number, scale: number): number {
  return Math.max(0, Math.min(100, 50 + (v / scale) * 50));
}

/**
 * Each arm against control, paired by session: its own net a session (toned by sign), trades a
 * session, the interval of its difference from control drawn untoned around zero, and the verdict
 * the study's result file gave it.
 */
export function ArmIntervals({ report, result }: { report: BacktestRunReport; result?: StudyResult }) {
  const arms = Object.entries(report.arms).sort(([a], [b]) => Number(b === "control") - Number(a === "control") || a.localeCompare(b));
  const bounds = arms.flatMap(([, a]) => [...(a.vs_control?.ci ?? []), a.vs_control?.mean_diff ?? 0]).map((x) => Math.abs(x));
  const scale = Math.max(1, ...bounds);
  const word = new Map((result?.verdicts ?? []).map((v) => [v.arm, v.verdict]));
  const wide = controlScale(report);
  return (
    <section>
      <h3>
        Each arm against control, paired by session <span className="muted">· the bar is the interval of the difference</span>
      </h3>
      <div className="study-arms r-intervals">
        {arms.map(([arm, a]) => {
          const v = a.vs_control;
          const per = tradesPerSession(report, arm);
          const verdict = arm === "control" ? null : (word.get(arm) ?? null);
          return (
            <div key={arm} className="study-arm r-interval-row">
              <span className="study-arm-name">{arm}</span>
              <span className={`study-arm-mean ${a.summary.mean === null ? "" : a.summary.mean < 0 ? "pnl-neg" : "pnl-pos"}`}>{fmtCash(a.summary.mean)} / session</span>
              <span className="muted">{per === null ? "" : `${fmtTrades(per)} trades / session`}</span>
              <span className="r-bar" aria-label={v?.ci ? `${arm} against control: ${fmtCash(v.ci[0])} to ${fmtCash(v.ci[1])}` : `${arm}: not paired`}>
                <span className="r-bar-zero" />
                {arm !== "control" && v?.ci ? (
                  <>
                    <span className="r-bar-ci" style={{ left: `${String(at(v.ci[0], scale))}%`, width: `${String(Math.max(0.5, at(v.ci[1], scale) - at(v.ci[0], scale)))}%` }} />
                    {v.mean_diff !== null ? <span className="r-bar-pt" style={{ left: `${String(at(v.mean_diff, scale))}%` }} /> : null}
                  </>
                ) : null}
              </span>
              <span>{arm === "control" ? <span className="chip muted">control</span> : verdict ? <span className={verdictChip(verdict)}>{verdict}</span> : a.no_exposure ? <span className="chip muted">measured nothing</span> : null}</span>
            </div>
          );
        })}
      </div>
      <div className="r-axis">
        <span>worse than control</span>
        <span>0</span>
        <span>better than control</span>
      </div>
      {wide !== null ? (
        <p className="muted study-section-note">
          Control trades about {Math.round(wide)} times as often a session as these arms (the module&apos;s own sampling arm), so each
          pairing with it is wide for that reason. A study of these arms judges them against its own base arm, not control.
        </p>
      ) : null}
    </section>
  );
}

/** Chips to pick the arm the health and calendar tabs read; control last. */
function ArmPicker({ arms, arm, onPick }: { arms: string[]; arm: string; onPick: (a: string) => void }) {
  return (
    <div className="history-controls" role="group" aria-label="Arm">
      {arms.map((a) => (
        <button key={a} type="button" className={a === arm ? "chip chip-ok" : "chip"} aria-pressed={a === arm} onClick={() => onPick(a)}>
          {a}
        </button>
      ))}
    </div>
  );
}

function RunSettings({ r }: { r: BacktestRunReport }) {
  const rows: Array<[string, string]> = [
    ["window", String(r.window)],
    ["span", span(r.span)],
    ["cadence", `${String(r.cadence)} s`],
    ["fill model", measure(r.fill_model)],
    ["trials (module)", r.trials_for_module === null ? "—" : String(r.trials_for_module)],
    ["refused sessions", String(Object.keys(r.refused).length)],
    ["code commit", r.code_commit ?? "—"],
    ["data fingerprint", r.data_fingerprint ?? "—"],
  ];
  const strips = Object.entries(r.cards?.arms ?? {})
    .map(([arm, c]) => settingsLine(arm, c.settings))
    .filter((x): x is string => x !== null);
  return (
    <>
      <dl className="study-fields">
        {rows.map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <dt>{k}</dt>
            <dd className="r-mono">{v}</dd>
          </div>
        ))}
      </dl>
      {strips.length > 0 ? (
        <section>
          <h3>Each arm&apos;s settings</h3>
          <ul className="study-list">
            {strips.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}

export function RunDialogBody({
  listing,
  report,
  hit,
  tab,
  onTab,
  arm,
  onArm,
  position,
  onPrev,
  onNext,
  onClose,
  status,
}: {
  listing: BacktestRunListing;
  report: BacktestRunReport | null;
  hit?: StudyHit;
  tab: RunTab;
  onTab: (t: RunTab) => void;
  arm: string | null;
  onArm: (a: string) => void;
  position: string;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  /** Shown in place of the body while the report loads or when it cannot be read. */
  status?: ReactNode;
}) {
  const cards = report?.cards?.arms ?? {};
  const armNames = Object.keys(cards).sort((a, b) => Number(a === "control") - Number(b === "control") || a.localeCompare(b));
  const chosen = arm !== null && armNames.includes(arm) ? arm : (armNames[0] ?? null);
  const flags = chosen !== null ? (cards[chosen]?.health?.flags ?? 0) : 0;
  const tabs: LightboxTab<RunTab>[] = [
    { id: "summary", label: "Summary" },
    { id: "equity", label: "Equity" },
    { id: "health", label: "Health", badge: flags > 0 ? `${String(flags)} flagged` : null },
    { id: "calendar", label: "Calendar" },
    { id: "settings", label: "Settings" },
    { id: "arms", label: "Arms" },
    { id: "paired", label: "Paired with control" },
    { id: "cards", label: "Run cards" },
    { id: "refused", label: "Refused", badge: report !== null && Object.keys(report.refused).length > 0 ? String(Object.keys(report.refused).length) : null },
  ];
  return (
    <LightboxFrame
      kicker={
        <>
          run {listing.run_id} · {etStamp(listing.created_at)} ET
        </>
      }
      title={hit ? hit.row.title : `A ${listing.module} replay, ${listing.window} window`}
      chips={
        <>
          {hit ? <span className={verdictChip(hit.result.summary)}>{hit.result.summary ?? "no verdict word"}</span> : null}
          <span className="chip muted">
            {listing.module} · {listing.window}
          </span>
          {listing.provisional === true ? <ProvisionalChip /> : null}
          {hit ? (
            <Link to={studyHref(hit.row.study)} className="study-dialog-link">
              {hit.row.study}
            </Link>
          ) : null}
          <LicenceFlag inline />
        </>
      }
      tabs={tabs}
      tab={tab}
      onTab={onTab}
      position={position}
      onPrev={onPrev}
      onNext={onNext}
      onClose={onClose}
    >
      {report === null ? (
        status
      ) : (
        <>
          {tab === "summary" ? (
            <>
              <RunPlainEnglish r={report} study={hit} />
              <ArmIntervals report={report} result={hit?.result} />
            </>
          ) : null}
          {tab === "equity" ? <EquityLines report={report} /> : null}
          {tab === "health" || tab === "calendar" ? (
            chosen === null ? (
              <p className="muted">This run&apos;s report carries no cards (an older backtest package).</p>
            ) : (
              <>
                <ArmPicker arms={armNames} arm={chosen} onPick={onArm} />
                {tab === "health" ? (
                  cards[chosen]?.health ? <HealthChecks c={cards[chosen]!} /> : <p className="muted">No health checks for this arm (an older backtest package).</p>
                ) : (
                  <MonthGrid c={cards[chosen]!} />
                )}
              </>
            )
          ) : null}
          {tab === "settings" ? <RunSettings r={report} /> : null}
          {tab === "arms" ? <ArmsTable arms={Object.entries(report.arms)} /> : null}
          {tab === "paired" ? <PairingTable arms={Object.entries(report.arms)} /> : null}
          {tab === "cards" ? <RunCardsSection id={listing.run_id} r={report} /> : null}
          {tab === "refused" ? <RefusedSessions r={report} /> : null}
        </>
      )}
    </LightboxFrame>
  );
}

/** The tab a run opens on: the old full-report link (`full=1`) lands on the run cards. */
export function initialRunTab(params: URLSearchParams): RunTab {
  return params.get("full") === "1" ? "cards" : "summary";
}

function RunDialog({ runs, index, studies, onStep, onClose }: { runs: BacktestRunListing[]; index: number; studies: StudyIndex; onStep: (to: number) => void; onClose: () => void }) {
  const [params] = useSearchParams();
  const [tab, setTab] = useState<RunTab>(initialRunTab(params));
  const [arm, setArm] = useState<string | null>(null);
  const listing = runs[index];
  const choices = useRunChoices();
  const { data, isLoading, isError, error } = useRun(listing?.run_id ?? null, { capital: choices.capital, risk: choices.risk, unit: choices.unit, dd: choices.dd });
  if (listing === undefined) return null;
  const report = data?.ok && data.data && data.data.run_id === listing.run_id ? data.data : null;
  const status = isLoading ? (
    <span className="skeleton skeleton-text" style={{ width: "50%" }} />
  ) : isError ? (
    <p className="muted">{(error as Error).message}</p>
  ) : report === null ? (
    <p className="muted">{data?.error ?? "Loading the report…"}</p>
  ) : null;
  return (
    <CardLightbox wide count={runs.length} index={index} onStep={onStep} onClose={onClose}>
      {(nav) => (
        <RunDialogBody
          listing={listing}
          report={report}
          hit={studies.byRun.get(listing.run_id)}
          tab={tab}
          onTab={setTab}
          arm={arm}
          onArm={setArm}
          position={nav.position}
          onPrev={nav.prev}
          onNext={nav.next}
          onClose={onClose}
          status={status}
        />
      )}
    </CardLightbox>
  );
}

/** The mosaic: module chips, then one tile per run, newest first and large. */
export function RunsMosaic({ d, studies, nowMs = etNowMs() }: { d: BacktestOverview; studies: StudyIndex; nowMs?: number }) {
  const [params, setParams] = useSearchParams();
  const view = useListView();
  const lightbox = useOpenParam("run");
  const modules = runModules(d.runs);
  const raw = params.get("module");
  const module = raw !== null && modules.includes(raw) ? raw : null;
  const runs = orderedRuns(d.runs, module);
  // A run named in the URL but filtered out still opens, stepping through every run.
  const stepRuns = runs.some((r) => r.run_id === lightbox.open) ? runs : orderedRuns(d.runs, null);
  const index = stepRuns.findIndex((r) => r.run_id === lightbox.open);
  const pickModule = (m: string | null) => {
    const next = new URLSearchParams(params);
    if (m === null) next.delete("module");
    else next.set("module", m);
    setParams(next, { replace: true });
  };
  if (d.runs.length === 0) return <p className="muted">No runs yet.</p>;
  return (
    <>
      <div className="history-controls study-chips" role="group" aria-label="Module">
        <button type="button" className={module === null ? "chip chip-ok" : "chip"} aria-pressed={module === null} onClick={() => pickModule(null)}>
          All {d.runs.length}
        </button>
        {modules.map((m) => (
          <button key={m} type="button" className={module === m ? "chip chip-ok" : "chip"} aria-pressed={module === m} onClick={() => pickModule(m)}>
            {m}
          </button>
        ))}
      </div>
      {view === "rows" ? (
        <BtRows rows={runs.map((r) => runRow(r, studies.byRun.get(r.run_id), nowMs, () => lightbox.show(r.run_id)))} />
      ) : (
        <div className="study-tiles">
          {runs.map((r, i) => (
            <RunTile key={r.run_id} r={r} hit={studies.byRun.get(r.run_id)} hero={i === 0} nowMs={nowMs} onOpen={() => lightbox.show(r.run_id)} />
          ))}
        </div>
      )}
      {index >= 0 ? <RunDialog runs={stepRuns} index={index} studies={studies} onStep={(to) => lightbox.step(stepRuns[to]?.run_id ?? "")} onClose={lightbox.close} /> : null}
    </>
  );
}

export function BacktestRunsTab() {
  const studies = useStudies();
  const index = indexStudies(studies.data?.ok ? studies.data.data : null);
  const [view, setView] = useViewChoice("backtest-runs");
  return (
    <WithOverview>
      {(d, updatedAt) => (
        <div className="cards cards-wide">
          <Card
            title="Runs"
            collapseKey="backtest-runs"
            updatedAt={updatedAt}
            controls={d.runs.length > 0 ? <ViewSwitch view={view} onChange={setView} /> : undefined}
            help={{
              title: "Reading the runs",
              body: (
                <>
                  <HelpSection title="One card per replay">
                    <p>
                      Newest first, linked to the study whose result file names it. A card opens the run in plain words, each arm against
                      control, its running net, health, months and settings, and then the full report as more tabs: every arm, the pairing
                      with control, the run cards (account, sizing, compounded lens, drawdowns, trades) and the refused sessions.
                    </p>
                    <p>The switch in the card head lists the runs as rows, one line a run; a row opens the same run.</p>
                  </HelpSection>
                </>
              ),
            }}
          >
            <p className="state-line state-warn bt-trials" style={{ fontSize: 12, margin: "0 0 4px" }}>
              {trialsLine(d)}
            </p>
            <ListViewContext.Provider value={view}>
              <RunsMosaic d={d} studies={index} />
            </ListViewContext.Provider>
          </Card>
        </div>
      )}
    </WithOverview>
  );
}
