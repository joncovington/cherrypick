import type { StudiesBoard, StudyResult, StudyRow } from "@console/shared";
import { useBridged } from "../../lib/bridged";

/**
 * The studies board as a lookup, for the Backtest tabs that point at a study (2026-10-29,
 * docs/studies-board-plan.md phase 4): the queue names a row's study and its verdict, the runs list
 * names the study a run belongs to. One fetch, shared through the query cache with the studies tab.
 */

export function useStudies() {
  return useBridged<StudiesBoard>(["backtest", "studies"], "/api/backtest/studies", 30_000);
}

export interface StudyIndex {
  byStudy: Map<string, StudyRow>;
  /** A run id to the study whose result file names it, and that file. */
  byRun: Map<string, { row: StudyRow; result: StudyResult }>;
}

export function indexStudies(board: StudiesBoard | null | undefined): StudyIndex {
  const byStudy = new Map<string, StudyRow>();
  const byRun = new Map<string, { row: StudyRow; result: StudyResult }>();
  for (const row of board?.rows ?? []) {
    byStudy.set(row.study, row);
    for (const result of row.results) if (result.runId) byRun.set(result.runId, { row, result });
  }
  return { byStudy, byRun };
}

/** The studies tab with one row opened. */
export function studyHref(study: string): string {
  return `/backtest/studies?study=${encodeURIComponent(study)}`;
}

/** The register's statuses that carry a verdict. */
export const VERDICT_STATUSES = ["passed", "failed", "no_verdict"];
