/**
 * The suite review: read the fact sets, render nothing new.
 *
 * This reader deliberately does almost no arithmetic. `packages/review` writes one versioned JSON
 * per session and every surface is supposed to read *that* — the alternative is what this console
 * already did once with `services/report.ts`, a hand-copied port of the orchestrator's per-schema
 * P&L rules whose own docstring flagged them as "copied exactly", and which had already drifted
 * (the orchestrator reads flies from `fly_positions`, that port reads `fly_books`).
 *
 * So: parse, shape for the page, and pass through. The one derived figure is the all-time roll-up,
 * which is a SUM OF THE FACT SETS rather than a fresh pass over the ledgers. That means its depth
 * is exactly what has been built — currently 2026-07-10 onward — and the payload states the range
 * it covers so the number is never read as all-of-history when it is not.
 *
 * Fact-set fields are read defensively: the artifact is versioned and will gain fields, and a
 * console that throws on an unfamiliar shape would take the page down for an additive change.
 */

import fs from "node:fs";
import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { num, suiteEra } from "./db.js";

export interface ReviewArm {
  arm: string;
  closed: number;
  net: number;
  wins: number;
  capitalAtRisk: number | null;
  onMaxRisk: number | null;
  /** The centring rule, when every entry used one — the tell for an arm that collapsed into another. */
  centredBy: string | null;
}

export interface ReviewModule {
  module: string;
  ok: boolean;
  reason: string | null;
  loopTicked: boolean | null;
  iterations: number | null;
  errors: number | null;
  closed: number;
  net: number;
  gross: number;
  cost: number;
  wins: number;
  capitalAtRisk: number | null;
  onMaxRisk: number | null;
  n: number | null;
  effectiveN: number | null;
  /** null = the module does not track breaks at all, which is weaker than an empty list. */
  breaks: string[] | null;
  suspectedBreak: { ratio: number; trades: number; trailingMedian: number } | null;
  expectedBasis: string | null;
  expected: number | null;
  observed: number | null;
  carriedPositions: number;
  carriedCapital: number | null;
  arms: ReviewArm[];
}

export interface ReviewSession {
  session: string;
  status: string;
  factVersion: number | null;
  generatedAt: string | null;
  modules: ReviewModule[];
  /** The narrative, if one has been written. Interpretation — never mixed into the numbers above. */
  note: string | null;
}

export interface ReviewPayload {
  sessions: string[];
  current: ReviewSession | null;
  /**
   * Era totals — the sum of fact sets from the suite's declared era (`data_epoch`) onward.
   *
   * This was `allTime` until 2026-08-21: a sum over every artifact ever built. The advisor-era
   * cutover retired every hand-designed arm at that boundary, so a total pooled across it reads as
   * one experiment when it is really two incomparable ones. `eraFrom` names the boundary on the
   * payload so the page can say what the number covers; a null `eraFrom` (no declared epoch) falls
   * back to everything, labeled accordingly.
   */
  era: {
    eraFrom: string | null;
    eraNote: string | null;
    sessions: number;
    from: string | null;
    to: string | null;
    netByModule: Record<string, number>;
    closedByModule: Record<string, number>;
    /** Per-module per-session nets in session order — the sparkline series. Same pass as the
     *  totals above, so the line and the tile it sits under cannot disagree. */
    trendByModule: Record<string, Array<{ session: string; net: number }>>;
    /** Suite net per session (every readable module summed) — the Overview calendar strip's
     *  series. A session with no readable module is ABSENT, never a zero day. */
    suiteDaily: Array<{ session: string; net: number; closed: number }>;
  };
}

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function readFacts(dir: string, session: string): Record<string, unknown> | null {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, `eod-${session}.json`), "utf-8"));
  } catch {
    return null;
  }
}

function readNote(dir: string, session: string): string | null {
  try {
    return fs.readFileSync(path.join(dir, `eod-${session}.note.md`), "utf-8");
  } catch {
    return null;
  }
}

export function listSessions(config: ConsoleConfig): string[] {
  try {
    return fs
      .readdirSync(config.paths.reviewDir)
      .filter((f) => f.startsWith("eod-") && f.endsWith(".json"))
      .map((f) => f.slice(4, -5))
      .sort();
  } catch {
    return [];
  }
}

function shapeArms(byProfile: Record<string, unknown>): ReviewArm[] {
  return Object.entries(byProfile)
    .map(([arm, raw]) => {
      const g = rec(raw);
      const ret = rec(g["return"]);
      return {
        arm,
        closed: num(g["closed"]) ?? 0,
        net: num(g["net"]) ?? 0,
        wins: num(g["wins"]) ?? 0,
        capitalAtRisk: num(ret["capital_at_risk"]),
        onMaxRisk: num(ret["on_max_risk"]),
        centredBy: typeof g["centred_by"] === "string" ? (g["centred_by"] as string) : null,
      };
    })
    .sort((a, b) => b.net - a.net);
}

function shapeModule(name: string, raw: unknown): ReviewModule {
  const m = rec(raw);
  if (m["ok"] !== true) {
    return {
      module: name,
      ok: false,
      reason: typeof m["reason"] === "string" ? (m["reason"] as string) : "unreadable",
      loopTicked: null, iterations: null, errors: null,
      closed: 0, net: 0, gross: 0, cost: 0, wins: 0,
      capitalAtRisk: null, onMaxRisk: null, n: null, effectiveN: null,
      breaks: null, suspectedBreak: null,
      expectedBasis: null, expected: null, observed: null,
      carriedPositions: 0, carriedCapital: null, arms: [],
    };
  }
  const results = rec(m["results"]);
  const ret = rec(m["return"]);
  const sample = rec(m["sample"]);
  const health = rec(m["health"]);
  const expected = rec(m["expected_vs_observed"]);
  const carried = rec(m["carried_overnight"]);
  const suspected = rec(sample["suspected_break"]);

  return {
    module: name,
    ok: true,
    reason: null,
    loopTicked: typeof health["loop_ticked"] === "boolean" ? (health["loop_ticked"] as boolean) : null,
    iterations: num(health["iterations"]),
    errors: num(health["errors"]),
    closed: num(results["closed"]) ?? 0,
    net: num(results["net"]) ?? 0,
    gross: num(results["gross"]) ?? 0,
    cost: num(results["cost"]) ?? 0,
    wins: num(results["wins"]) ?? 0,
    capitalAtRisk: num(ret["capital_at_risk"]),
    onMaxRisk: num(ret["on_max_risk"]),
    n: num(sample["n"]),
    effectiveN: num(sample["effective_n"]),
    // Preserve null-vs-empty: null means the module tracks no breaks at all.
    breaks: Array.isArray(sample["breaks"]) ? (sample["breaks"] as string[]) : null,
    suspectedBreak: num(suspected["ratio"])
      ? {
          ratio: num(suspected["ratio"]) as number,
          trades: num(suspected["trades"]) ?? 0,
          trailingMedian: num(suspected["trailing_median_trades"]) ?? 0,
        }
      : null,
    expectedBasis: typeof expected["basis"] === "string" ? (expected["basis"] as string) : null,
    expected: num(expected["expected"]),
    observed: num(expected["observed"]),
    carriedPositions: num(carried["positions"]) ?? 0,
    carriedCapital: num(carried["capital_at_risk"]),
    arms: shapeArms(rec(m["by_profile"])),
  };
}

export function readReview(config: ConsoleConfig, session?: string): ReviewPayload {
  const dir = config.paths.reviewDir;
  const sessions = listSessions(config);
  const chosen = session && sessions.includes(session) ? session : sessions[sessions.length - 1];

  let current: ReviewSession | null = null;
  if (chosen) {
    const facts = readFacts(dir, chosen);
    if (facts) {
      const modules = rec(facts["modules"]);
      current = {
        session: chosen,
        status: typeof facts["status"] === "string" ? (facts["status"] as string) : "unknown",
        factVersion: num(facts["fact_version"]),
        generatedAt: typeof facts["generated_at"] === "string" ? (facts["generated_at"] as string) : null,
        modules: Object.keys(modules).map((name) => shapeModule(name, modules[name])),
        note: readNote(dir, chosen),
      };
    }
  }

  // Era totals are a sum of the artifacts, never a fresh pass over the ledgers — so they cannot
  // disagree with the per-session view. Bounded to the declared era (data_epoch): the advisor-era
  // cutover retired every hand-designed arm at 2026-08-21, and pooling across that boundary reads
  // as one experiment when it is really two incomparable ones.
  const era = suiteEra(config.paths.orchestratorConfig);
  const eraSessions = era.from === null ? sessions : sessions.filter((s) => era.from !== null && s >= era.from);
  const netByModule: Record<string, number> = {};
  const closedByModule: Record<string, number> = {};
  // The per-session series come out of the SAME pass as the totals, so a sparkline and the tile it
  // sits under can never disagree, and reading them costs no extra artifact reads.
  const trendByModule: Record<string, Array<{ session: string; net: number }>> = {};
  const suiteDaily: Array<{ session: string; net: number; closed: number }> = [];
  for (const s of eraSessions) {
    const facts = readFacts(dir, s);
    if (!facts) continue;
    const modules = rec(facts["modules"]);
    let dayNet = 0;
    let dayClosed = 0;
    let dayReadable = false;
    for (const [name, raw] of Object.entries(modules)) {
      const m = rec(raw);
      if (m["ok"] !== true) continue;
      const results = rec(m["results"]);
      const net = num(results["net"]) ?? 0;
      const closed = num(results["closed"]) ?? 0;
      netByModule[name] = (netByModule[name] ?? 0) + net;
      closedByModule[name] = (closedByModule[name] ?? 0) + closed;
      (trendByModule[name] ??= []).push({ session: s, net: Math.round(net * 100) / 100 });
      dayNet += net;
      dayClosed += closed;
      dayReadable = true;
    }
    // A session where nothing was readable is left OUT of the suite series rather than pushed as a
    // zero day — the page's own null-is-not-zero rule applied to the series, so a broken artifact
    // reads as a gap in the strip and never as a flat day.
    if (dayReadable) {
      suiteDaily.push({ session: s, net: Math.round(dayNet * 100) / 100, closed: dayClosed });
    }
  }

  return {
    sessions,
    current,
    era: {
      eraFrom: era.from,
      eraNote: era.note,
      sessions: eraSessions.length,
      from: eraSessions[0] ?? null,
      to: eraSessions[eraSessions.length - 1] ?? null,
      netByModule,
      closedByModule,
      trendByModule,
      suiteDaily,
    },
  };
}
