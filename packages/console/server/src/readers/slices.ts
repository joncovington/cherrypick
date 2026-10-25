import fs from "node:fs";
import path from "node:path";
import type { BridgedResult, SliceCell, SliceReport, SliceReports } from "@console/shared";
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

function toCell(value: string, v: unknown): SliceCell {
  const r = rec(v);
  const ci = r["ci95"];
  return {
    value,
    n: num(r["n"]) ?? 0,
    mean: num(r["mean"]),
    ci95: Array.isArray(ci) && ci.length === 2 && ci.every((x) => typeof x === "number") ? [ci[0] as number, ci[1] as number] : null,
    greyed: r["greyed"] !== false,
  };
}

export function shapeSlice(file: string, raw: unknown): SliceReport | null {
  const r = rec(raw);
  if (r["in_sample"] !== true || typeof r["unit"] !== "string") return null;
  return {
    file,
    label: typeof r["label"] === "string" ? r["label"] : file,
    unit: r["unit"],
    trades: num(r["trades"]) ?? 0,
    cells: num(r["cells"]) ?? 0,
    rankedCells: num(r["ranked_cells"]) ?? 0,
    overall: toCell("all", r["overall"]),
    dimensions: Object.entries(rec(r["dimensions"])).map(([name, cells]) => ({
      name,
      cells: Object.entries(rec(cells)).map(([value, c]) => toCell(value, c)),
    })),
    note: typeof r["note"] === "string" ? r["note"] : "",
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
