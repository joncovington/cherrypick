import type { StudiesBoard, StudyResult, StudyRow } from "@console/shared";
import { useBridged } from "../../lib/bridged";

/**
 * The studies board as a lookup, for the Backtest tabs that point at a study:
 * the queue names a row's study and its verdict, the runs list
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

/** The register's status in words: `no_verdict` -> "no verdict". */
export function statusWord(status: string): string {
  return status.replace(/_/g, " ");
}

/** What a result file covers: its window, else its span's years, else its name. */
export function resultLabel(r: StudyResult): string {
  if (r.window) return r.window;
  if (r.span) {
    const [a, b] = [r.span[0].slice(0, 4), r.span[1].slice(0, 4)];
    return a === b ? a : `${a}–${b}`;
  }
  return r.name;
}

/**
 * A register title as a heading and the rest, split at its first colon: "The wing width of MEIC's
 * live rule" / "15, 20, 25 and 30 against 10". A title without one is all heading.
 */
export function splitTitle(title: string): [string, string | null] {
  const at = title.indexOf(": ");
  return at > 0 ? [title.slice(0, at), title.slice(at + 2)] : [title, null];
}

/**
 * A verdict word's chip: a pass, a fail, anything else plain. `verdict-pass` / `verdict-fail` colour it
 * on the Backtest page (blue and orange, 2026-11-03), so a verdict never reads as a P&L's sign.
 */
export function verdictChip(word: string | null): string {
  const w = (word ?? "").toLowerCase();
  if (w === "pass" || w === "passed") return "chip chip-ok verdict-pass";
  if (w === "fail" || w === "failed") return "chip chip-missing verdict-fail";
  return word === null ? "chip muted" : "chip";
}
