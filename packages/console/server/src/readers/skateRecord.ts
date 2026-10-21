/**
 * The skate screen's forward record for the Skate tab (docs/skate-plan.md step 4).
 *
 * `technicals.skate_record`, run each morning by the `technicals-skate` job, keeps the rows the tab
 * showed, settles them at expiry from the history store and writes its reading beside the bands as
 * `data/technicals/skate/record.json`. This reads that file and renames its fields; it decides
 * nothing. `decision_grade` and each band's `passes` are the file's, never re-derived from the z
 * scores and the thresholds, so the tab can only say what the record says.
 *
 * Its own route rather than a field on `/api/screeners/skate`: the record is one file for the whole
 * screen, not a session's, and the run reader is memoised on a ~4.5 MB session file it has nothing to
 * do with. The per-row table (`record.db`) is not read here.
 *
 * A missing file and an unreadable one are stated, never shown as zeros.
 */

import fs from "node:fs";
import path from "node:path";
import type { SkateBand, SkateRecordBand, SkateRecordGroup, SkateRecordPayload, SkateRecordTest } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num } from "./db.js";

/** The file's band names against ours. */
const FILE_BAND: Record<SkateBand, string> = { fixed: "fixed", byExpiry: "by_expiry" };

function rec(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

export function skateRecordFile(config: ConsoleConfig): string {
  return path.join(config.paths.technicalsDir, "skate", "record.json");
}

function group(v: unknown): SkateRecordGroup | null {
  const g = rec(v);
  if (g === null) return null;
  return { n: num(g["n"]), skated: num(g["skated"]), rate: num(g["rate"]), implied: num(g["implied"]), z: num(g["z"]) };
}

function band(v: unknown): SkateRecordBand | null {
  const b = rec(v);
  if (b === null) return null;
  const passes = b["passes"];
  return {
    highGap: group(b["high_gap"]),
    belowZero: group(b["below_zero"]),
    zHighOverLow: num(b["z_high_over_low"]),
    // Only an explicit boolean is a verdict: anything else is "none stated", never a pass.
    passes: passes === true ? true : passes === false ? false : null,
    tested: num(b["tested"]),
  };
}

function test(v: unknown): SkateRecordTest | null {
  const t = rec(v);
  if (t === null) return null;
  return { gapHigh: num(t["gap_high"]), gapLow: num(t["gap_low"]), minExpiries: num(t["min_expiries"]), zOneSided: num(t["z_one_sided"]) };
}

const EMPTY: SkateRecordPayload = {
  status: "none",
  writtenAt: null,
  recorded: null,
  added: {},
  settledNow: null,
  settled: null,
  expiries: null,
  decisionGrade: false,
  bands: { fixed: null, byExpiry: null },
  test: null,
};

export function readSkateRecord(config: ConsoleConfig): SkateRecordPayload {
  const file = skateRecordFile(config);
  let writtenAt: string;
  try {
    writtenAt = fs.statSync(file).mtime.toISOString();
  } catch {
    return EMPTY;
  }
  let doc: Record<string, unknown> | null;
  try {
    doc = rec(JSON.parse(fs.readFileSync(file, "utf-8")));
  } catch {
    doc = null;
  }
  const verdict = doc === null ? null : rec(doc["verdict"]);
  if (doc === null || verdict === null) return { ...EMPTY, status: "unreadable", writtenAt };
  const bands = rec(verdict["bands"]) ?? {};
  const now = rec(doc["settled_now"]);
  const added: Record<string, number> = {};
  for (const [k, v] of Object.entries(rec(doc["added"]) ?? {})) {
    const n = num(v);
    if (n !== null) added[k] = n;
  }
  return {
    status: "ok",
    writtenAt,
    recorded: num(doc["recorded"]),
    added,
    settledNow:
      now === null ? null : { skated: num(now["skated"]), tested: num(now["tested"]), through: num(now["through"]), waiting: num(now["waiting"]) },
    settled: num(verdict["settled"]),
    expiries: num(verdict["expiries"]),
    decisionGrade: verdict["decision_grade"] === true,
    bands: { fixed: band(bands[FILE_BAND.fixed]), byExpiry: band(bands[FILE_BAND.byExpiry]) },
    test: test(doc["test"]),
  };
}
