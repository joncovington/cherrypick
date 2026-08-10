/**
 * The champions & challengers read: per-module, per-tag calibration readings
 * over normalized closed-trade records, with the same qualification checks the
 * orchestrator's calibrate applies. Per-schema normalization is copied from
 * report.py's readers exactly — the net rules and the capital-at-risk formula
 * are the load-bearing parts.
 */

import fs from "node:fs";
import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb } from "../readers/db.js";
import {
  calibrationReading,
  qualifyOne,
  recommendChampion,
  type CalibrationReading,
  type NormalizedRecord,
  type ChampionVerdict,
  type Qualification,
} from "../analytics/calibration.js";

export interface ModuleCalibration {
  module: string;
  champion: string | null;
  /** Per tag: the evidence bundle plus its qualification checks. */
  tags: Array<{ tag: string; reading: CalibrationReading; qualification: Qualification; role: string }>;
  verdict: ChampionVerdict | null;
}

function readChampionMap(config: ConsoleConfig): Record<string, string | null> {
  try {
    const raw = JSON.parse(fs.readFileSync(config.paths.orchestratorConfig, "utf-8")) as Record<string, unknown>;
    const modules = raw["modules"];
    const out: Record<string, string | null> = {};
    if (Array.isArray(modules)) {
      for (const m of modules as Array<Record<string, unknown>>) {
        out[String(m["id"] ?? m["name"] ?? "")] = typeof m["champion"] === "string" ? m["champion"] : null;
      }
    } else if (typeof modules === "object" && modules !== null) {
      for (const [k, v] of Object.entries(modules as Record<string, Record<string, unknown>>)) {
        out[k] = typeof v?.["champion"] === "string" ? v["champion"] : null;
      }
    }
    return out;
  } catch {
    return {};
  }
}

/** MEIC: net = pnl − fees; capital = (wing − credit) × multiplier × qty; tag = risk_profile. */
function meicRecords(config: ConsoleConfig): Record<string, NormalizedRecord[]> {
  const dbPath = path.join(config.paths.meicDir, "paper_trades.db");
  return withReadOnlyDb<Record<string, NormalizedRecord[]>>(dbPath, {}, (db) => {
    const rows = db
      .prepare<[], Record<string, unknown>>(
        `SELECT risk_profile, pnl, fees, exit_time, slippage_dollars, wing_width, net_credit, quantity, dollar_multiplier
           FROM ic_trades WHERE exit_time IS NOT NULL`,
      )
      .all();
    const out: Record<string, NormalizedRecord[]> = {};
    for (const r of rows) {
      const tag = String(r["risk_profile"] ?? "untagged");
      const wing = typeof r["wing_width"] === "number" ? r["wing_width"] : null;
      const mult = typeof r["dollar_multiplier"] === "number" && r["dollar_multiplier"] ? r["dollar_multiplier"] : 100;
      const qty = typeof r["quantity"] === "number" ? r["quantity"] : 1;
      const cap = wing !== null ? (wing - Number(r["net_credit"] ?? 0)) * mult * qty : null;
      (out[tag] ??= []).push({
        netPnl: Number(r["pnl"] ?? 0) - Number(r["fees"] ?? 0),
        capital: cap !== null && cap > 0 ? Math.round(cap * 100) / 100 : null,
        session: String(r["exit_time"] ?? "").slice(0, 10),
        slippage: typeof r["slippage_dollars"] === "number" ? r["slippage_dollars"] : null,
      });
    }
    return out;
  });
}

/** Earnings: net = pnl − entry_cost − exit_cost; capital = capital_at_risk; tag = profile. */
function earningsRecords(config: ConsoleConfig): Record<string, NormalizedRecord[]> {
  const dbPath = path.join(config.paths.earningsDir, "paper_trades.db");
  return withReadOnlyDb<Record<string, NormalizedRecord[]>>(dbPath, {}, (db) => {
    const rows = db
      .prepare<[], Record<string, unknown>>(
        `SELECT profile, strategy, pnl, entry_cost, exit_cost, closed_at, entry_slippage, exit_slippage, capital_at_risk
           FROM trades WHERE closed_at IS NOT NULL`,
      )
      .all();
    const out: Record<string, NormalizedRecord[]> = {};
    for (const r of rows) {
      const tag = String(r["profile"] ?? r["strategy"] ?? "untagged");
      const slips = [r["entry_slippage"], r["exit_slippage"]].filter((v): v is number => typeof v === "number");
      const closedAt = r["closed_at"];
      const session =
        typeof closedAt === "number"
          ? new Date(closedAt * 1000).toISOString().slice(0, 10)
          : String(closedAt ?? "").slice(0, 10);
      (out[tag] ??= []).push({
        netPnl: Number(r["pnl"] ?? 0) - Number(r["entry_cost"] ?? 0) - Number(r["exit_cost"] ?? 0),
        capital: typeof r["capital_at_risk"] === "number" ? r["capital_at_risk"] : null,
        session,
        slippage: slips.length > 0 ? slips.reduce((s, v) => s + v, 0) : null,
      });
    }
    return out;
  });
}

/** Flies: net = gross − fees on settled rows; slippage and capital deliberately unknown. */
function fliesRecords(config: ConsoleConfig): Record<string, NormalizedRecord[]> {
  const dbPath = path.join(config.paths.fliesDir, "paper_trades.db");
  return withReadOnlyDb<Record<string, NormalizedRecord[]>>(dbPath, {}, (db) => {
    const rows = db
      .prepare<[], Record<string, unknown>>(
        "SELECT arm, gross_pnl, fees, trade_date FROM fly_positions WHERE status = 'settled'",
      )
      .all();
    const out: Record<string, NormalizedRecord[]> = {};
    for (const r of rows) {
      const tag = String(r["arm"] ?? "untagged");
      (out[tag] ??= []).push({
        netPnl: Number(r["gross_pnl"] ?? 0) - Number(r["fees"] ?? 0),
        capital: null,
        session: String(r["trade_date"] ?? ""),
        slippage: null,
      });
    }
    return out;
  });
}

function roleOf(tag: string, champion: string | null, q: Qualification, verdict: ChampionVerdict | null): string {
  if (champion !== null && tag === champion) return "champion";
  if (verdict !== null && verdict.challengers[tag]?.beatsChampion === true) return "beats champion";
  return q.qualified ? "qualified" : "not qualified";
}

export function buildCalibration(config: ConsoleConfig): ModuleCalibration[] {
  const champions = readChampionMap(config);
  const sources: Array<[string, Record<string, NormalizedRecord[]>]> = [
    ["meic", meicRecords(config)],
    ["earnings", earningsRecords(config)],
    ["flies", fliesRecords(config)],
  ];
  return sources.map(([module, grouped]) => {
    const readings: Record<string, CalibrationReading> = {};
    for (const [tag, records] of Object.entries(grouped)) {
      if (records.length === 0) continue;
      readings[tag] = calibrationReading(records);
    }
    const champion = champions[module] ?? null;
    // A module with a declared champion gets the promotion comparison; parallel
    // arms are qualified independently and never promoted against each other.
    const verdict = champion !== null ? recommendChampion(readings, champion) : null;
    const tags = Object.entries(readings)
      .map(([tag, reading]) => {
        const qualification = qualifyOne(reading);
        return { tag, reading, qualification, role: roleOf(tag, champion, qualification, verdict) };
      })
      .sort((a, b) => b.reading.netPnl - a.reading.netPnl);
    return { module, champion, tags, verdict };
  });
}
