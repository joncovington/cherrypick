import fs from "node:fs";
import path from "node:path";
import type {
  BridgedResult,
  SliceAnalysis,
  SliceCell,
  SliceDimensionRead,
  SlicePlacebo,
  SliceReport,
  SliceReports,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";

/**
 * B5's slice reports: the JSON files `python -m cherrypick.technicals slices` writes under
 * `data/technicals/slices/`. Read-only; the cells, bins and greying are `core.slices`'. Behind the
 * Backtest gate at the route, because a report may be built from a licensed replay.
 */

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function toPlacebo(v: unknown): SlicePlacebo | null {
  const p = rec(v);
  const sessions = num(p["sessions"]);
  const of = num(p["of"]);
  const beatenBy = num(p["beaten_by"]);
  return sessions === null || of === null || beatenBy === null ? null : { sessions, of, beatenBy };
}

function toCell(value: string, v: unknown): SliceCell {
  const r = rec(v);
  const ci = r["ci95"];
  return {
    value,
    n: num(r["n"]) ?? 0,
    mean: num(r["mean"]),
    ci95: Array.isArray(ci) && ci.length === 2 && ci.every((x) => typeof x === "number") ? [ci[0] as number, ci[1] as number] : null,
    greyed: r["greyed"] !== false,
    sessions: num(r["sessions"]),
    placebo: toPlacebo(r["placebo"]),
  };
}

/** One dimension's reading for the plain-English card: does any ranked group's interval clear
 *  another's? Ranked = not greyed and with an interval. */
export function readDimension(name: string, cells: SliceCell[]): SliceDimensionRead {
  const ranked = cells.filter((c) => !c.greyed && c.ci95 !== null && c.mean !== null);
  const greyed = cells.length - ranked.length;
  if (cells.length <= 1) return { name, read: "one-group", best: cells[0] ?? null, worst: null, ranked: ranked.length, greyed };
  if (ranked.length <= 1) return { name, read: "too-few", best: ranked[0] ?? null, worst: null, ranked: ranked.length, greyed };
  // Highest lower bound against lowest upper bound: if one sits above the other, at least that pair
  // of groups does not overlap.
  const top = ranked.reduce((a, b) => (b.ci95![0] > a.ci95![0] ? b : a));
  const bottom = ranked.reduce((a, b) => (b.ci95![1] < a.ci95![1] ? b : a));
  if (top !== bottom && top.ci95![0] > bottom.ci95![1]) {
    return { name, read: "separated", best: top, worst: bottom, ranked: ranked.length, greyed };
  }
  const hi = ranked.reduce((a, b) => (b.mean! > a.mean! ? b : a));
  const lo = ranked.reduce((a, b) => (b.mean! < a.mean! ? b : a));
  return { name, read: "overlapping", best: hi, worst: lo, ranked: ranked.length, greyed };
}

export function analyse(label: string, overall: SliceCell, dimensions: { name: string; cells: SliceCell[] }[]): SliceAnalysis {
  const parts = label.split("/");
  const [schema, arm, month] = parts.length === 3 ? parts : [null, null, null];
  const ci = overall.ci95;
  const reads = dimensions.map((d) => readDimension(d.name, d.cells));
  return {
    schema: schema ?? null,
    arm: arm ?? null,
    month: month === "all" || month === undefined ? null : month,
    overall: ci === null ? "unclear" : ci[0] > 0 ? "positive" : ci[1] < 0 ? "negative" : "unclear",
    dimensions: reads,
    greyedCells: reads.reduce((s, d) => s + d.greyed, 0),
  };
}

export function shapeSlice(file: string, raw: unknown): SliceReport | null {
  const r = rec(raw);
  if (r["in_sample"] !== true || typeof r["unit"] !== "string") return null;
  const label = typeof r["label"] === "string" ? r["label"] : file;
  const overall = toCell("all", r["overall"]);
  const dimensions = Object.entries(rec(r["dimensions"])).map(([name, cells]) => ({
    name,
    cells: Object.entries(rec(cells)).map(([value, c]) => toCell(value, c)),
  }));
  return {
    file,
    label,
    unit: r["unit"],
    trades: num(r["trades"]) ?? 0,
    cells: num(r["cells"]) ?? 0,
    rankedCells: num(r["ranked_cells"]) ?? 0,
    overall,
    dimensions,
    note: typeof r["note"] === "string" ? r["note"] : "",
    analysis: analyse(label, overall, dimensions),
  };
}

export function slicesDir(config: ConsoleConfig): string {
  return path.join(config.paths.technicalsDir, "slices");
}

export function readSlices(config: ConsoleConfig): BridgedResult<SliceReports> {
  const dir = slicesDir(config);
  let files: string[] = [];
  try {
    files = fs.readdirSync(dir).filter((f) => f.endsWith(".json")).sort();
  } catch {
    return { ok: true, data: { dir, reports: [] }, error: null };
  }
  const reports: SliceReport[] = [];
  for (const f of files) {
    try {
      const shaped = shapeSlice(f, JSON.parse(fs.readFileSync(path.join(dir, f), "utf-8")));
      if (shaped) reports.push(shaped);
    } catch {
      // An unreadable file is skipped, not fatal: the others still read.
    }
  }
  return { ok: true, data: { dir, reports }, error: null };
}
