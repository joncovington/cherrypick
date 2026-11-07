import { type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { BacktestOverview, StudiesBoard, StudyGroup, StudyRow } from "@console/shared";
import { HelpSection, HelpWarn } from "../../components/CardHelp";
import { Card } from "../../components/DataTable";
import { useBridged } from "../../lib/bridged";
import { MiniMarkdown } from "../../lib/miniMarkdown";
import { StudyCards, notesHref, notesName, planUrl } from "./StudyCards";
import { resultLabel, statusWord, studyHref, useStudies } from "./studyIndex";

/**
 * The Backtest page's studies tab (2026-10-29, docs/studies-board-plan.md; as cards 2026-11-03): what
 * we tested and what we found. One tile per hypothesis-register item, read through
 * `/api/backtest/studies` (the orchestrator's `hypotheses-board`), opening to a lightbox with its
 * decision, verdicts, run figures, grid, declaration, rivals and queue rows (`StudyCards.tsx`).
 *
 * Every word is the orchestrator's: the register's status, the study's own verdict, the group. A
 * file with no verdict word says so; nothing is worked out here from figures.
 */

export { notesHref, notesName, planUrl, resultLabel, statusWord };

/** One line for a row's results: "design fail · holdout fail". */
export function resultsLine(row: StudyRow): string {
  if (row.results.length === 0) return "—";
  return row.results
    .map((r) => `${resultLabel(r)} ${!r.present ? "missing" : (r.summary ?? "see plan")}`)
    .join(" · ");
}

/** "27 studies: 2 in progress, 2 declared, 20 finished (18 failed, 2 no verdict), 3 observations." */
export function headline(board: StudiesBoard): string {
  const count = (g: StudyGroup) => board.rows.filter((r) => r.group === g).length;
  const finished = board.rows.filter((r) => r.group === "finished");
  const by = ["passed", "failed", "no_verdict", "retired"]
    .map((s) => [s, finished.filter((r) => r.status === s).length] as const)
    .filter(([, n]) => n > 0)
    .map(([s, n]) => `${n} ${statusWord(s)}`);
  const parts = [
    `${count("in_progress")} in progress`,
    `${count("declared")} declared`,
    `${finished.length} finished${by.length ? ` (${by.join(", ")})` : ""}`,
    `${count("observation")} observation${count("observation") === 1 ? "" : "s"}`,
  ];
  return `${board.rows.length} studies: ${parts.join(", ")}.`;
}

/** The design and holdout windows, as one line. */
export function windowsLine(o: BacktestOverview | null): string | null {
  const w = o?.windows;
  return w?.design && w.holdout ? `Design window ${w.design[0]} to ${w.design[1]}; holdout ${w.holdout[0]} to ${w.holdout[1]}.` : null;
}

/** True while no validation has unlocked history: every MEIC and flies replay is provisional. */
export function historyLocked(o: BacktestOverview | null): boolean {
  return o !== null && o.validation.length > 0 && o.validation.every((v) => !v.unlocks_history);
}

/** The study context the overview holds: the windows, and why replays are provisional. */
export function contextLine(o: BacktestOverview | null): string | null {
  if (o === null) return null;
  const windows = windowsLine(o) ?? "";
  const locked = historyLocked(o);
  const why = locked ? " MEIC and flies replays are provisional: validation has not unlocked their history (see “is history trusted?” on the data tab)." : "";
  return windows + why || null;
}

/** One study's notes, full width, with the way back to its card. */
export function StudyNotes({ board, study, resultName }: { board: StudiesBoard; study: string; resultName: string }) {
  const row = board.rows.find((r) => r.study === study);
  const result = row?.results.find((r) => r.name === resultName);
  return (
    <div className="study-notes-page">
      <p style={{ margin: "0 0 8px" }}>
        <Link to={studyHref(study)}>← back to {row ? `${row.id} ${row.title}` : study}</Link>
      </p>
      {result?.notes ? (
        <>
          <p className="muted" style={{ margin: "0 0 8px" }}>
            {notesName(resultName)}, beside {resultName} on this machine. Licensed figures: never shared.
          </p>
          <MiniMarkdown source={result.notes} />
        </>
      ) : (
        <p className="muted">No {notesName(resultName)} for {study} on this machine.</p>
      )}
    </div>
  );
}

export function BacktestStudiesTab() {
  const { data, isLoading, isError, error, dataUpdatedAt } = useStudies();
  const [params] = useSearchParams();
  const overview = useBridged<BacktestOverview>(["backtest", "overview"], "/api/backtest/overview", 60_000);
  const ov = overview.data?.ok ? overview.data.data : null;
  const context = windowsLine(ov);
  const provisional = historyLocked(ov);
  let body: ReactNode;
  if (isLoading) {
    body = (
      <div className="study-tiles" aria-busy="true">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="study-tile study-tile-skeleton skeleton" />
        ))}
      </div>
    );
  } else if (isError) body = <p className="muted">{(error as Error).message}</p>;
  else if (data === undefined || !data.ok || data.data === null) body = <p className="muted">{data?.error ?? "studies board read unavailable"}</p>;
  else if (params.get("study") && params.get("notes")) {
    body = <StudyNotes board={data.data} study={params.get("study")!} resultName={params.get("notes")!} />;
  } else {
    const b = data.data;
    body = (
      <>
        <p style={{ margin: "0 0 4px" }}>{headline(b)}</p>
        {context ? (
          <p className="muted" style={{ margin: "0 0 8px" }}>
            {context}
          </p>
        ) : null}
        {b.unregistered.length > 0 ? (
          <p className="pnl-neg" style={{ margin: "0 0 8px" }}>
            Ran with no register row: {b.unregistered.join(", ")}. Add each to <code>docs/hypotheses.json</code>.
          </p>
        ) : null}
        {b.rows.length === 0 ? <p className="muted">The register has no rows.</p> : <StudyCards board={b} />}
      </>
    );
  }
  return (
    <Card
      title="Studies"
      collapseKey="backtest-studies"
      updatedAt={dataUpdatedAt}
      isError={isError}
      flag={provisional ? "provisional history" : undefined}
      help={{
        title: "Reading the studies",
        body: (
          <>
            {provisional ? (
              <HelpWarn title="Provisional">
                <p>
                  MEIC and flies replays are provisional: validation has not unlocked their history, so a verdict over it is not yet
                  evidence (see &ldquo;is history trusted?&rdquo; on the data tab).
                </p>
              </HelpWarn>
            ) : null}
            <HelpSection title="One card per register item">
              <p>
                One card per item in the hypothesis register (<code>docs/hypotheses.json</code>). A verdict is the word the study wrote in
                its result file; the files stay on this machine.
              </p>
            </HelpSection>
            <HelpSection title="Opening a card">
              <p>A card opens to its run&apos;s figures, pass test, rivals, per-arm verdicts, queue rows and links.</p>
            </HelpSection>
          </>
        ),
      }}
    >
      {body}
    </Card>
  );
}
