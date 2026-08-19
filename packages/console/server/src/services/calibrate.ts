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
import { advisedTagStatus, fliesAdviceDecl, meicAdviceDecl, type AdviceDecl } from "../readers/adviceDecl.js";
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
  tags: Array<{
    tag: string;
    reading: CalibrationReading;
    qualification: Qualification;
    role: string;
    /** "unknown" when this module has no reliable machine-readable source for it (earnings: three
        independently-hardcoded Python strategy lists, no JSON flag, and a profile/strategy grain
        mismatch -- see readEarningsStrategyStatus). Never guessed at; a wrong "retired" badge on a
        still-trading arm is worse than no badge. */
    status: "active" | "retired" | "unknown";
  }>;
  verdict: ChampionVerdict | null;
}

/** MEIC: packages/meic/config.risk.json's profiles.<tag>.enabled -- the literal switch
 *  paper.py's all_profile_names() reads each tick (`.get("enabled", True) is not False`), not
 *  just documentation. Lives in the module's own source tree, not ~/.cherrypick. Null (not {})
 *  on any read/parse failure, so a missing/broken file reports "unknown" for every tag rather
 *  than silently mislabeling every currently-active tag "retired". */
function readMeicProfileStatus(config: ConsoleConfig): Record<string, boolean> | null {
  try {
    const raw = JSON.parse(fs.readFileSync(config.paths.meicRiskConfig, "utf-8")) as Record<string, unknown>;
    const profiles = raw["profiles"];
    if (typeof profiles !== "object" || profiles === null) return null;
    const out: Record<string, boolean> = {};
    for (const [tag, v] of Object.entries(profiles as Record<string, unknown>)) {
      if (tag.startsWith("_")) continue;
      const enabled = (v as Record<string, unknown> | undefined)?.["enabled"];
      out[tag] = enabled !== false;
    }
    return out;
  } catch {
    return null;
  }
}

/** Flies: ~/.cherrypick/config/flies.json's arms.<tag>.enabled -- what cli.py's enabled_arms()
 *  reads. A tag with no entry in this object at all (retired arms like iron/bwb-atm are dropped
 *  from the deployed config, not kept with enabled:false) counts the same as enabled:false. Null
 *  (not {}) on any read/parse failure -- see readMeicProfileStatus for why that distinction
 *  matters. */
function readFliesArmStatus(config: ConsoleConfig): Record<string, boolean> | null {
  try {
    const raw = JSON.parse(fs.readFileSync(config.paths.fliesConfig, "utf-8")) as Record<string, unknown>;
    const arms = raw["arms"];
    if (typeof arms !== "object" || arms === null) return null;
    const out: Record<string, boolean> = {};
    for (const [tag, v] of Object.entries(arms as Record<string, unknown>)) {
      if (tag.startsWith("_") || typeof v !== "object" || v === null) continue;
      const enabled = (v as Record<string, unknown>)["enabled"];
      out[tag] = enabled !== false;
    }
    return out;
  } catch {
    return null;
  }
}

/** Per-module tag-status lookup, keyed the same way ModuleCalibration's own `module` field is.
 *  Earnings deliberately has no entry: retirement there is a strategy-level fact inferred from
 *  three hardcoded Python lists, while Champions' tags are profile-level (`default`/`strat_test`/
 *  `strat_test:<strategy>`) -- a mixed-strategy profile can't be classified active-or-retired at
 *  this grain without guessing, so every earnings tag reports "unknown" rather than a badge that
 *  might be wrong. */
function tagStatusReaders(config: ConsoleConfig): Record<string, Record<string, boolean> | null> {
  return {
    meic: readMeicProfileStatus(config),
    flies: readFliesArmStatus(config),
  };
}

/** Per-module advice declaration, for the `advised:<base>` tags the registry readers above can
 *  never see: those books are synthesized at session start by each paper loop, not declared as
 *  profiles/arms, so registry absence is their NORMAL state and must not read as retirement. */
function advisedDeclReaders(config: ConsoleConfig): Record<string, AdviceDecl | null> {
  return {
    meic: meicAdviceDecl(config),
    flies: fliesAdviceDecl(config),
  };
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
  const statusReaders = tagStatusReaders(config);
  const advisedDecls = advisedDeclReaders(config);
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
    const statusOf = statusReaders[module];
    const advisedOf = advisedDecls[module] ?? null;
    const tags = Object.entries(readings)
      .map(([tag, reading]) => {
        const qualification = qualifyOne(reading);
        // Advised books first: `advised:<base>` is synthesized by the paper loop from the module
        // config's advice block and is never a registry entry, so the registry rule below would
        // badge the actively-trading advised book "retired". Its status is the advice block's.
        // For everything else: statusOf null/undefined means no source for this module, or the
        // source failed to read -- either way "unknown" for every tag, never a guessed "retired".
        // statusOf present but this TAG absent from it: a real fact (the config no longer lists
        // it at all) -- retired.
        const status: "active" | "retired" | "unknown" = tag.startsWith("advised:")
          ? advisedTagStatus(tag, advisedOf)
          : statusOf == null ? "unknown" : statusOf[tag] === undefined ? "retired" : statusOf[tag] ? "active" : "retired";
        return { tag, reading, qualification, role: roleOf(tag, champion, qualification, verdict), status };
      })
      .sort((a, b) => b.reading.netPnl - a.reading.netPnl);
    return { module, champion, tags, verdict };
  });
}
