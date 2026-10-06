import fs from "node:fs";
import path from "node:path";
import type {
  AgentArmDay,
  AgentCheck,
  AgentConfigView,
  AgentCriterion,
  AgentDecisionNow,
  AgentGateRefusal,
  AgentOpenTag,
  AgentPaired,
  AgentQualification,
  AgentShadow,
  AgentSpend,
  AgentTaggedClose,
  FliesAgentPack,
  FliesAgentPayload,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { hasColumn, hasTable, num, obj, readOnlyDb, str } from "./db.js";

/**
 * The flies intraday agent's page (docs/intraday-agent-plan.md in packages/flies).
 *
 * Four sources, all the module's, none re-derived:
 * - the qualification file (`data/flies/intraday_agent_qualification.json`): criteria, paired
 *   statistics, every session's three arms, the settled tagged closes and the spend, all computed by
 *   `cherrypick.flies.intraday_eval` at each settlement. The page's verdicts are its `pass` values
 *   and `offered_modes`; nothing here judges.
 * - the agent's record of the session (`data/flies/intraday_agent/<session>.jsonl`), one line per
 *   check: shaped, never summed (the spend over sessions is the file's).
 * - the decision file (`state/flies-intraday-advice-paper.json`), and whether it is still the one
 *   the arm follows: unexpired and for the session in view, the rule `read_decision` applies.
 * - the paper ledger, for what the file cannot hold yet: closes tagged on positions still open and
 *   the trend gate's refusals today. Queries only, guarded by `sqlite_master`/column checks so an
 *   older ledger reads as "none", not as a swallowed throw.
 */

const SESSION = /^\d{4}-\d{2}-\d{2}$/;
const RECORD = /^(\d{4}-\d{2}-\d{2})\.jsonl$/;
const QUALIFICATION = "intraday_agent_qualification.json";
const DECISION = "flies-intraday-advice-paper.json";
/** The engine's refusal for the trend gate (`engine.py`, `completion_against_drift`). */
const GATE_REFUSAL = "completion_against_drift";

export function isAgentSession(s: unknown): s is string {
  return typeof s === "string" && SESSION.test(s);
}

function recordDir(config: ConsoleConfig): string {
  return path.join(config.paths.fliesDir, "intraday_agent");
}

function etDate(d: Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(d);
}

const ET_PARTS = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** An epoch as New York wall clock, `YYYY-MM-DDTHH:MM:SS`: the form the ledgers write and the chart
 *  family's `minuteOf` reads off characters 11-16. A UTC ISO string there would read four or five
 *  hours out. */
export function etWallClock(epochSeconds: number): string {
  const p = Object.fromEntries(ET_PARTS.formatToParts(new Date(epochSeconds * 1000)).map((x) => [x.type, x.value]));
  return `${p["year"]}-${p["month"]}-${p["day"]}T${p["hour"]}:${p["minute"]}:${p["second"]}`;
}

function isoOfEpoch(v: unknown): string | null {
  const n = num(v);
  return n === null ? null : etWallClock(n);
}

function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

function gate(v: unknown): "on" | "off" | null {
  return v === "on" || v === "off" ? v : null;
}

// --------------------------------------------------------------------------- qualification file
function armDay(v: unknown): AgentArmDay | null {
  if (typeof v !== "object" || v === null) return null;
  const o = obj(v);
  return {
    entries: num(o["entries"]) ?? 0,
    stranded: num(o["stranded"]) ?? 0,
    tagged: num(o["tagged"]) ?? 0,
    settledNet: num(o["settled_net"]) ?? 0,
    netCloses: num(o["net_closes"]) ?? 0,
    netCloses2x: num(o["net_closes_2x"]) ?? 0,
  };
}

function paired(v: unknown): AgentPaired {
  const o = obj(v);
  return {
    n: num(o["n"]) ?? 0,
    mean: num(o["mean"]),
    sd: num(o["sd"]),
    lower95: num(o["lower_95"]),
    sessionsNeeded: num(o["sessions_needed"]),
  };
}

function criterion(v: unknown): AgentCriterion | null {
  const o = obj(v);
  const id = str(o["id"]);
  const mode = o["mode"];
  if (id === null || (mode !== "gates" && mode !== "gates_and_closures")) return null;
  return {
    id,
    mode,
    label: str(o["label"]) ?? id,
    value: num(o["value"]),
    threshold: num(o["threshold"]),
    pass: bool(o["pass"]),
  };
}

function taggedClose(v: unknown): AgentTaggedClose | null {
  const o = obj(v);
  const id = str(o["position_id"]);
  const day = str(o["trade_date"]);
  if (id === null || day === null) return null;
  return {
    arm: str(o["arm"]) ?? "",
    positionId: id,
    tradeDate: day,
    side: str(o["side"]),
    center: num(o["center"]),
    wingWidth: num(o["wing_width"]),
    quantity: num(o["quantity"]),
    credit: num(o["credit"]),
    source: str(o["source"]),
    taggedAt: str(o["tagged_at"]),
    spot: num(o["spot"]),
    natural: num(o["natural"]),
    mid: num(o["mid"]),
    fees: num(o["fees"]),
    settledNet: num(o["settled_net"]) ?? 0,
    closedNet: num(o["closed_net"]) ?? 0,
    closedNet2x: num(o["closed_net_2x"]) ?? 0,
    saved: num(o["saved"]) ?? 0,
  };
}

function spendRow(v: unknown): AgentSpend | null {
  const o = obj(v);
  const session = str(o["session"]);
  const target = o["target"];
  if (session === null || (target !== "paper" && target !== "live")) return null;
  const byModel: AgentSpend["byModel"] = {};
  for (const [m, x] of Object.entries(obj(o["by_model"]))) {
    const mo = obj(x);
    byModel[m] = { calls: num(mo["calls"]) ?? 0, costUsd: num(mo["cost_usd"]) ?? 0 };
  }
  return {
    session,
    target,
    checks: num(o["checks"]) ?? 0,
    calls: num(o["calls"]) ?? 0,
    ok: num(o["ok"]) ?? 0,
    costUsd: num(o["cost_usd"]) ?? 0,
    byModel,
  };
}

function shadowOf(v: unknown): AgentShadow {
  const o = obj(v);
  const closes = obj(o["closes"]);
  return {
    sessions: (Array.isArray(o["sessions"]) ? o["sessions"] : [])
      .map((x) => {
        const r = obj(x);
        const session = str(r["session"]);
        if (session === null) return null;
        return {
          session,
          entries: num(r["entries"]) ?? 0,
          wouldRefuse: num(r["would_refuse"]) ?? 0,
          liveEffect: num(r["live_effect"]) ?? 0,
          paperEffect: num(r["paper_effect"]),
          counted: r["counted"] === true,
          agree: bool(r["agree"]),
        };
      })
      .filter((x): x is NonNullable<typeof x> => x !== null),
    counted: num(o["counted"]) ?? 0,
    agree: num(o["agree"]) ?? 0,
    agreement: num(o["agreement"]),
    closes: { tagged: num(closes["tagged"]) ?? 0, saved: num(closes["saved"]), saved2x: num(closes["saved_2x"]) },
  };
}

/** The file shaped into the shared type. Every value is the writer's; absent keys read as null. */
export function shapeQualification(raw: Record<string, unknown>): AgentQualification {
  const arms = obj(raw["arms"]);
  const sessions = obj(raw["sessions"]);
  const closes = obj(raw["closes"]);
  const strand = obj(raw["strand_rate"]);
  const unlocked: Record<string, boolean> = {};
  for (const [k, v] of Object.entries(obj(raw["unlocked"]))) if (typeof v === "boolean") unlocked[k] = v;
  const perSession = Array.isArray(raw["per_session"]) ? raw["per_session"] : [];
  return {
    generatedAt: str(raw["generated_at"]),
    arms: {
      control: str(arms["control"]) ?? "control",
      rule: str(arms["rule"]) ?? "trend-rule",
      agent: str(arms["agent"]) ?? "intraday-agent",
      live: str(arms["live"]),
    },
    liveModeMax: str(raw["live_mode_max"]),
    trendBandPoints: num(raw["trend_band_points"]),
    sessions: {
      decided: num(sessions["decided"]) ?? 0,
      paired: strList(sessions["paired"]),
      shadow: strList(sessions["shadow"]),
    },
    gates: { ...paired(raw["gates"]), targetEdge: num(obj(raw["gates"])["target_edge"]) },
    closes: { ...paired(closes), episodes: num(closes["episodes"]) ?? 0, saved2x: num(closes["saved_2x"]) },
    strandRate: { rule: num(strand["rule"]), agent: num(strand["agent"]) },
    shadow: shadowOf(raw["shadow"]),
    liveClosesBuilt: bool(raw["live_closes_built"]),
    criteria: (Array.isArray(raw["criteria"]) ? raw["criteria"] : [])
      .map(criterion)
      .filter((c): c is AgentCriterion => c !== null),
    unlocked,
    offeredModes: strList(raw["offered_modes"]),
    perSession: perSession
      .map((v) => {
        const o = obj(v);
        const session = str(o["session"]);
        if (session === null) return null;
        const rule = armDay(o["rule"]);
        const agent = armDay(o["agent"]);
        return {
          session,
          decided: o["decided"] === true,
          control: armDay(o["control"]),
          rule,
          agent,
          agentLessRule: rule !== null && agent !== null ? Math.round((agent.settledNet - rule.settledNet) * 100) / 100 : null,
        };
      })
      .filter((r): r is NonNullable<typeof r> => r !== null),
    taggedCloses: (Array.isArray(raw["tagged_closes"]) ? raw["tagged_closes"] : [])
      .map(taggedClose)
      .filter((c): c is AgentTaggedClose => c !== null),
    spend: (Array.isArray(raw["spend"]) ? raw["spend"] : [])
      .map(spendRow)
      .filter((s): s is AgentSpend => s !== null),
  };
}

function readQualification(config: ConsoleConfig): {
  status: FliesAgentPayload["qualificationStatus"];
  value: AgentQualification | null;
} {
  const p = path.join(config.paths.fliesDir, QUALIFICATION);
  if (!fs.existsSync(p)) return { status: "absent", value: null };
  try {
    const raw = JSON.parse(fs.readFileSync(p, "utf-8")) as unknown;
    if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return { status: "failed", value: null };
    return { status: "ok", value: shapeQualification(raw as Record<string, unknown>) };
  } catch {
    return { status: "failed", value: null };
  }
}

// --------------------------------------------------------------------------- the session's record
function readRecordLines(config: ConsoleConfig, session: string): unknown[] {
  let text: string;
  try {
    text = fs.readFileSync(path.join(recordDir(config), `${session}.jsonl`), "utf-8");
  } catch {
    return [];
  }
  // A torn last line (the job is mid-append) is skipped, as `intraday_advice.records` skips it; the
  // index stays the line's position so the pack endpoint finds the same record.
  return text.split(/\r?\n/).map((line) => {
    if (line.trim() === "") return null;
    try {
      return JSON.parse(line) as unknown;
    } catch {
      return null;
    }
  });
}

export function shapeCheck(v: unknown, index: number): AgentCheck | null {
  if (typeof v !== "object" || v === null) return null;
  const o = obj(v);
  const at = isoOfEpoch(o["as_of"]);
  if (at === null) return null;
  const d = obj(o["decision"]);
  const spx = obj(obj(o["pack"])["spx"]);
  return {
    at,
    target: str(o["target"]) ?? "paper",
    called: o["called"] === true,
    skipped: str(o["skipped"]),
    trigger: str(o["trigger"]),
    model: str(o["model"]),
    costUsd: num(o["cost_usd"]),
    seconds: num(o["seconds"]),
    ok: bool(o["ok"]),
    error: str(o["error"]),
    trendGate: gate(d["trend_gate"]),
    closeLabels: strList(d["close_labels"]),
    droppedCloses: (Array.isArray(d["dropped_closes"]) ? d["dropped_closes"] : [])
      .map((x) => str(obj(x)["label"]))
      .filter((x): x is string => x !== null),
    confidence: num(d["confidence"]),
    reason: str(d["reason"]),
    spot: num(spx["last"]),
    index,
  };
}

function dayOpenOf(lines: unknown[]): number | null {
  for (const v of lines) {
    const open = num(obj(obj(obj(v)["pack"])["spx"])["open"]);
    if (open !== null) return open;
  }
  return null;
}

function readDecision(config: ConsoleConfig, session: string, now: Date): AgentDecisionNow | null {
  let raw: unknown;
  try {
    raw = JSON.parse(fs.readFileSync(path.join(config.paths.cherrypick, "state", DECISION), "utf-8"));
  } catch {
    return null;
  }
  const o = obj(raw);
  const d = obj(o["decision"]);
  const expires = num(o["expires_at"]);
  const asOf = num(o["as_of"]);
  const forSession = str(o["session"]);
  return {
    session: forSession,
    at: isoOfEpoch(asOf),
    ageSeconds: asOf === null ? null : Math.max(0, Math.round(now.getTime() / 1000 - asOf)),
    expiresAt: isoOfEpoch(expires),
    // `read_decision`'s rule: the session matches, it has not expired, and it was admissible.
    fresh: forSession === session && expires !== null && now.getTime() / 1000 <= expires && o["ok"] === true,
    ok: bool(o["ok"]),
    trendGate: gate(d["trend_gate"]),
    closeLabels: strList(d["close_labels"]),
    reason: str(d["reason"]),
    model: str(o["model"]),
    error: str(o["error"]),
  };
}

function readAgentConfig(config: ConsoleConfig): AgentConfigView | null {
  let raw: unknown;
  try {
    raw = JSON.parse(fs.readFileSync(config.paths.fliesConfig, "utf-8"));
  } catch {
    return null;
  }
  const block = obj(obj(raw)["intraday_agent"]);
  // As declared, never defaulted here: a key the file leaves out reads null ("module default").
  return {
    enabled: block["enabled"] === true,
    model: str(block["model"]),
    paper: block["paper"] !== false,
    paperArm: str(block["paper_arm"]),
    liveModeMax: str(block["live_mode_max"]),
    triggerBandPoints: num(block["trigger_band_points"]),
    decisionTtlMinutes: num(block["decision_ttl_minutes"]),
    maxCallsPerSession: num(block["max_calls_per_session"]),
    minMinutesBetweenCalls: num(block["min_minutes_between_calls"]),
  };
}

// --------------------------------------------------------------------------- the paper ledger
function readLedger(
  config: ConsoleConfig,
  session: string,
  arms: string[],
): { openTags: AgentOpenTag[]; refusals: AgentGateRefusal[] } {
  const empty = { openTags: [], refusals: [] };
  const marks = arms.map(() => "?").join(", ");
  const outcome = readOnlyDb(path.join(config.paths.fliesDir, "paper_trades.db"), (db) => {
    const openTags: AgentOpenTag[] = [];
    if (hasTable(db, "fly_positions") && hasColumn(db, "fly_positions", "close_tag_at")) {
      const rows = db
        .prepare(
          `SELECT position_id, arm, side, center, wing_width, quantity, credit, close_tag_source, close_tag_at,
                  close_tag_spot, close_tag_natural, close_tag_mid
             FROM fly_positions
            WHERE trade_date = ? AND arm IN (${marks}) AND close_tag_at IS NOT NULL AND status = 'open'
            ORDER BY close_tag_at`,
        )
        .all(session, ...arms) as Record<string, unknown>[];
      for (const r of rows) {
        openTags.push({
          positionId: str(r["position_id"]) ?? "",
          arm: str(r["arm"]) ?? "",
          side: str(r["side"]),
          center: num(r["center"]),
          wingWidth: num(r["wing_width"]),
          quantity: num(r["quantity"]),
          credit: num(r["credit"]),
          source: str(r["close_tag_source"]),
          taggedAt: str(r["close_tag_at"]),
          spot: num(r["close_tag_spot"]),
          natural: num(r["close_tag_natural"]),
          mid: num(r["close_tag_mid"]),
        });
      }
    }
    const refusals: AgentGateRefusal[] = [];
    if (hasTable(db, "fly_entry_attempts")) {
      const rows = db
        .prepare(
          `SELECT ts, arm, center, spot FROM fly_entry_attempts
            WHERE trade_date = ? AND arm IN (${marks}) AND outcome = 'gate_blocked' AND block_detail = ?
            ORDER BY ts`,
        )
        .all(session, ...arms, GATE_REFUSAL) as Record<string, unknown>[];
      for (const r of rows) {
        const at = str(r["ts"]);
        if (at !== null) refusals.push({ at, arm: str(r["arm"]) ?? "", center: num(r["center"]), spot: num(r["spot"]) });
      }
    }
    return { openTags, refusals };
  });
  return outcome.status === "ok" ? outcome.value : empty;
}

// --------------------------------------------------------------------------- the payload
function listSessions(config: ConsoleConfig, q: AgentQualification | null): string[] {
  const out = new Set<string>(q?.perSession.map((r) => r.session) ?? []);
  try {
    for (const n of fs.readdirSync(recordDir(config))) {
      const m = RECORD.exec(n);
      if (m?.[1] !== undefined) out.add(m[1]);
    }
  } catch {
    // no records yet
  }
  return [...out].sort().reverse();
}

export function readFliesAgent(config: ConsoleConfig, session: string | null, now: Date = new Date()): FliesAgentPayload {
  const q = readQualification(config);
  const sessions = listSessions(config, q.value);
  const today = etDate(now);
  const day = session ?? (sessions.includes(today) ? today : (sessions[0] ?? null));
  const agentConfig = readAgentConfig(config);
  const arms = q.value !== null ? [q.value.arms.rule, q.value.arms.agent] : ["trend-rule", agentConfig?.paperArm ?? "intraday-agent"];
  const lines = day !== null ? readRecordLines(config, day) : [];
  const ledger = day !== null ? readLedger(config, day, arms) : { openTags: [], refusals: [] };
  const checks = lines.map((v, i) => shapeCheck(v, i)).filter((c): c is AgentCheck => c !== null);
  const paper = checks.filter((c) => c.target === "paper");
  return {
    session: day,
    sessions,
    config: agentConfig,
    decision: day !== null ? readDecision(config, day, now) : null,
    checks,
    sessionSpend: {
      checks: paper.length,
      calls: paper.filter((c) => c.called).length,
      costUsd: Math.round(paper.reduce((sum, c) => sum + (c.called ? (c.costUsd ?? 0) : 0), 0) * 10000) / 10000,
    },
    dayOpen: dayOpenOf(lines),
    openTags: ledger.openTags,
    refusals: ledger.refusals,
    qualification: q.value,
    qualificationStatus: q.status,
    now: now.toISOString(),
  };
}

/** Every `_`-prefixed key removed, as `intraday_pack.for_model` removes them: the pack the model saw. */
export function forModel(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(forModel);
  if (typeof v === "object" && v !== null) {
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) if (!k.startsWith("_")) out[k] = forModel(x);
    return out;
  }
  return v;
}

export function readFliesAgentPack(config: ConsoleConfig, session: string, index: number): FliesAgentPack | null {
  const line = readRecordLines(config, session)[index];
  const pack = obj(line)["pack"];
  if (pack === undefined) return null;
  return { session, index, pack: forModel(pack) };
}
