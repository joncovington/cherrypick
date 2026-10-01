/**
 * The System page's health, supervisor and modules tabs. Read-only over the files the suite
 * itself writes; nothing here runs Python or touches a process.
 *
 * Verdicts are the suite's, not the console's: a tile takes its level from the watchdog finding
 * for that subsystem, or from the suite's own rule where it has one (the supervisor heartbeat,
 * `orchestrator/supersnap.py`). A job row's level only colours facts the registry recorded — a
 * hold, a backoff, a non-zero exit that was not a requested stop.
 */

import fs from "node:fs";
import path from "node:path";
import type {
  SupervisorDaemon,
  SupervisorJob,
  SystemCheck,
  SystemFinding,
  SystemHealthPayload,
  SystemLevel,
  SystemModuleRow,
  SystemModulesPayload,
  SystemSupervisorPayload,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readJson } from "./db.js";
import { tailFile } from "./logs.js";
import { pidAlive, readAlertDaemonHealth } from "../services/alertDaemon.js";
import { readLockStatus, sessionDateEt } from "../services/liveLock.js";
import { streamerFreshness } from "./streamcache.js";

/** The supervisor counts as alive under this heartbeat age, with its pid present (`supersnap.py`). */
const SUPERVISOR_ALIVE_S = 90;

function state(config: ConsoleConfig, ...parts: string[]): string {
  return path.join(config.paths.cherrypick, "state", ...parts);
}

function ageOf(iso: string | null): number | null {
  if (iso === null) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Math.max(0, (Date.now() - t) / 1000);
}

function epochIso(v: unknown): string | null {
  return typeof v === "number" && Number.isFinite(v) ? new Date(v * 1000).toISOString() : null;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function mtimeAge(p: string): number | null {
  try {
    return Math.max(0, (Date.now() - fs.statSync(p).mtimeMs) / 1000);
  } catch {
    return null;
  }
}

function readPid(p: string): number | null {
  try {
    const n = Number.parseInt(fs.readFileSync(p, "utf-8").trim(), 10);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

export function ageText(s: number | null): string {
  if (s === null) return "never";
  if (s < 90) return `${String(Math.round(s))}s`;
  if (s < 5400) return `${String(Math.round(s / 60))}m`;
  if (s < 172_800) return `${String(Math.round(s / 3600))}h`;
  return `${String(Math.round(s / 86_400))}d`;
}

function findingLevel(status: string | null | undefined): SystemLevel {
  if (status === "OK") return "ok";
  if (status === "WARN") return "warn";
  if (status === "CRITICAL" || status === "ERROR") return "critical";
  return "unknown";
}

// --------------------------------------------------------------------------- watchdog

interface Watchdog {
  at: string | null;
  overall: string | null;
  findings: SystemFinding[];
  byKey: Map<string, SystemFinding>;
}

function readWatchdog(config: ConsoleConfig): Watchdog {
  const raw = readJson(config.paths.watchdogLast);
  // `watchdog_state.json` is the renotify memory: per key, the status and when it was first seen.
  // A key whose remembered status matches today's is how long the problem has stood.
  const memory = readJson(state(config, "watchdog_state.json")) ?? {};
  const findings: SystemFinding[] = Array.isArray(raw?.["findings"])
    ? (raw["findings"] as Array<Record<string, unknown>>).map((f) => {
        const key = String(f["key"] ?? "");
        const status = String(f["status"] ?? "");
        const mem = memory[key] as Record<string, unknown> | undefined;
        return {
          key,
          status,
          title: String(f["title"] ?? ""),
          message: String(f["message"] ?? ""),
          since: status !== "OK" && mem?.["status"] === status ? str(mem["first_seen"]) : null,
        };
      })
    : [];
  return {
    at: str(raw?.["ts"]),
    overall: str(raw?.["overall"]),
    findings,
    byKey: new Map(findings.map((f) => [f.key, f])),
  };
}

// --------------------------------------------------------------------------- supervisor

function jobState(row: Record<string, unknown>, held: boolean, now: number): { state: string; level: SystemLevel } {
  const failures = num(row["consecutive_failures"]) ?? 0;
  const backoff = num(row["backoff_until"]);
  const exit = num(row["last_exit_code"]);
  const requested = row["last_exit_requested"] === true;
  if (held) return { state: "held", level: "warn" };
  if (num(row["running_pid"]) !== null) return { state: "running", level: "ok" };
  if (row["enabled"] === false) return { state: "disabled", level: "unknown" };
  if (backoff !== null && backoff * 1000 > now) return { state: "backoff", level: failures >= 3 ? "critical" : "warn" };
  if (failures > 0 || (exit !== null && exit !== 0 && !requested)) {
    return { state: "failing", level: failures >= 3 ? "critical" : "warn" };
  }
  const resident = str(row["resident_state"]);
  if (resident !== null) return { state: resident, level: "ok" };
  return { state: "idle", level: "ok" };
}

function readHolds(config: ConsoleConfig): SystemSupervisorPayload["holds"] {
  const raw = readJson(state(config, "holds.json")) ?? {};
  return Object.entries(raw).map(([name, v]) => {
    const h = (v ?? {}) as Record<string, unknown>;
    return { name, by: str(h["by"]), at: epochIso(h["at"]) ?? str(h["at"]) };
  });
}

function readRestartRequests(config: ConsoleConfig): SystemSupervisorPayload["restartRequests"] {
  let names: string[] = [];
  try {
    names = fs.readdirSync(state(config)).filter((n) => n.startsWith("restart-requested.") && n.endsWith(".json"));
  } catch {
    return [];
  }
  return names.map((n) => {
    const r = readJson(state(config, n)) ?? {};
    return {
      job: n.slice("restart-requested.".length, -".json".length),
      requestedAt: epochIso(r["requested_at"]) ?? str(r["requested_at"]),
      by: str(r["by"]),
    };
  });
}

function readJobs(config: ConsoleConfig, holds: Set<string>): {
  jobs: SupervisorJob[];
  writtenAt: string | null;
  deriveErrors: Record<string, string>;
} {
  const reg = readJson(state(config, "supervisor-jobs.json"));
  const rows = (reg?.["jobs"] ?? {}) as Record<string, Record<string, unknown>>;
  const now = Date.now();
  const jobs = Object.entries(rows).map(([id, row]): SupervisorJob => {
    const { state: s, level } = jobState(row, holds.has(id) || row["held"] != null, now);
    const restart = row["last_restart"] as Record<string, unknown> | null | undefined;
    const stderr = path.join(config.paths.cherrypick, "logs", "jobs", `${id}.stderr.log`);
    return {
      id,
      kind: str(row["kind"]),
      enabled: row["enabled"] !== false,
      enabledReason: str(row["enabled_reason"]),
      schedule: str(row["schedule"]),
      state: s,
      level,
      runningPid: num(row["running_pid"]),
      pidStartedAt: epochIso(row["pid_started_at"]),
      lastStart: str(row["last_start"]),
      lastExitCode: num(row["last_exit_code"]),
      lastExitAt: str(row["last_exit_at"]),
      lastExitRequested: row["last_exit_requested"] === true,
      consecutiveFailures: num(row["consecutive_failures"]) ?? 0,
      backoffUntil: epochIso(row["backoff_until"]),
      nextRun: str(row["next_run"]),
      lastFireDay: str(row["last_fire_day"]),
      missed: row["missed"] == null ? null : String(row["missed"]),
      lastError: str(row["last_error"]),
      lastRestart: restart ? { at: epochIso(restart["at"]), result: str(restart["result"]) } : null,
      stderrLog: fs.existsSync(stderr) ? `logs/jobs/${id}.stderr.log` : null,
    };
  });
  const errs = (reg?.["derive_errors"] ?? {}) as Record<string, unknown>;
  return {
    jobs: jobs.sort((a, b) => a.id.localeCompare(b.id)),
    writtenAt: str(reg?.["written_at"]),
    deriveErrors: Object.fromEntries(Object.entries(errs).map(([k, v]) => [k, String(v)])),
  };
}

function readDaemons(config: ConsoleConfig, wd: Watchdog): SupervisorDaemon[] {
  const data = path.join(config.paths.cherrypick, "data");
  const specs: Array<{ id: string; pidFile: string; heartbeat: string | null }> = [
    { id: "streamer", pidFile: path.join(data, "marketdata", "streamer.pid"), heartbeat: null },
    { id: "gex-recorder", pidFile: path.join(data, "gex", "recorder.pid"), heartbeat: path.join(data, "gex", "recorder.heartbeat") },
  ];
  return specs.map(({ id, pidFile, heartbeat }) => {
    const pid = readPid(pidFile);
    const launch = readJson(state(config, `service-${id}.launch.json`));
    // The watchdog's verdict names the streamer `streamer` and every other service `service.<id>`.
    const f = wd.byKey.get(id === "streamer" ? "streamer" : `service.${id}`);
    return {
      id,
      pid,
      alive: pid === null ? null : pidAlive(pid),
      launchedAt: epochIso(launch?.["stamped_at"]),
      heartbeatAgeSeconds: heartbeat !== null ? mtimeAge(heartbeat) : null,
      watchdog: f ? { status: f.status, note: f.message } : null,
    };
  });
}

function readHeartbeat(config: ConsoleConfig): SystemSupervisorPayload["heartbeat"] {
  const hb = readJson(state(config, "supervisor.last.json"));
  const pid = num(hb?.["pid"]);
  const ageSeconds = ageOf(str(hb?.["ts"]));
  const alivePid = pid === null ? null : pidAlive(pid);
  return {
    present: hb !== null,
    alive: ageSeconds !== null && ageSeconds < SUPERVISOR_ALIVE_S && alivePid === true,
    ageSeconds,
    pid,
    pidAlive: alivePid,
    startedAt: str(hb?.["started_at"]),
    loopSeq: num(hb?.["loop_seq"]),
    jobs: num(hb?.["jobs"]),
    residentChildren: num(hb?.["resident_children"]),
    rssMb: num(hb?.["rss_mb"]),
  };
}

export function readSystemSupervisor(config: ConsoleConfig): SystemSupervisorPayload {
  const wd = readWatchdog(config);
  const holds = readHolds(config);
  const { jobs, writtenAt, deriveErrors } = readJobs(config, new Set(holds.map((h) => h.name)));
  const anchor = wd.byKey.get("supervisor.anchor");
  const probe = readJson(state(config, "ensure_supervisor.json"));
  return {
    heartbeat: readHeartbeat(config),
    registry: { writtenAt, ageSeconds: ageOf(writtenAt), deriveErrors },
    jobs,
    holds,
    restartRequests: readRestartRequests(config),
    daemons: readDaemons(config, wd),
    anchor: {
      status: anchor?.status ?? null,
      message: anchor?.message ?? null,
      probeFailures: num(probe?.["failures"]),
    },
  };
}

// --------------------------------------------------------------------------- modules

function moduleIds(config: ConsoleConfig): Array<{ id: string; enabled: boolean; kind: string | null }> {
  const cfg = readJson(config.paths.orchestratorConfig) ?? {};
  const raw = cfg["modules"];
  const out: Array<{ id: string; enabled: boolean; kind: string | null }> = [];
  const push = (id: string, m: Record<string, unknown>) =>
    out.push({ id, enabled: m["enabled"] === true, kind: str(m["kind"]) });
  if (Array.isArray(raw)) for (const m of raw as Array<Record<string, unknown>>) push(String(m["id"] ?? m["name"] ?? "?"), m);
  else if (raw && typeof raw === "object") for (const [k, v] of Object.entries(raw)) push(k, (v ?? {}) as Record<string, unknown>);
  return out;
}

function armedToday(config: ConsoleConfig, id: string): { armed: boolean; date: string | null } {
  const arm = readJson(state(config, `${id}-live-arm.json`));
  const date = str(arm?.["date"]);
  // Armed means armed FOR TODAY, the Live page's rule: an older record is one the loop disarms on.
  return { armed: date !== null && date === sessionDateEt(), date };
}

export function readSystemModules(config: ConsoleConfig): SystemModulesPayload {
  const wd = readWatchdog(config);
  const holds = new Set(readHolds(config).map((h) => h.name));
  const { jobs } = readJobs(config, holds);
  const lock = readLockStatus(config);
  const gates = new Map(lock.modules.map((g) => [g.id, g.liveEnabled]));
  const data = path.join(config.paths.cherrypick, "data");

  const modules = moduleIds(config).map(({ id, enabled, kind }): SystemModuleRow => {
    const ledgerPath = path.join(data, id, "paper_trades.db");
    let ledger: SystemModuleRow["ledger"] = null;
    try {
      const st = fs.statSync(ledgerPath);
      ledger = { path: `data/${id}/paper_trades.db`, sizeBytes: st.size, ageSeconds: Math.max(0, (Date.now() - st.mtimeMs) / 1000) };
    } catch {
      ledger = null;
    }
    const reqPath = state(config, "stream_requests", `${id}.json`);
    const req = readJson(reqPath);
    const reqAge = mtimeAge(reqPath);
    return {
      id,
      enabled,
      kind,
      liveEnabled: gates.get(id) ?? null,
      armedToday: armedToday(config, id).armed,
      jobs: jobs
        .filter((j) => j.id === id || j.id.startsWith(`${id}-`))
        .map((j) => ({ id: j.id, state: j.state, level: j.level })),
      // The module writes its heartbeat in one of two formats (ISO or epoch text); the mtime is
      // the same instant either way and needs no parser per module.
      heartbeatAgeSeconds: mtimeAge(state(config, `${id}.heartbeat`)),
      ledger,
      streamRequest:
        req !== null && reqAge !== null
          ? {
              symbols: Array.isArray(req["symbols"]) ? req["symbols"].length : 0,
              legs: Array.isArray(req["legs"]) ? req["legs"].length : 0,
              ageSeconds: reqAge,
            }
          : null,
      findings: wd.findings
        .filter((f) => f.key.startsWith(`${id}.`))
        .map((f) => ({ key: f.key, status: f.status, message: f.message })),
    };
  });
  return { modules };
}

// --------------------------------------------------------------------------- health

function lastNotify(config: ConsoleConfig): SystemHealthPayload["notify"]["last"] {
  const lines = tailFile(path.join(config.paths.cherrypick, "logs", "notify.log"));
  const raw = lines[lines.length - 1];
  if (raw === undefined) return null;
  try {
    const j = JSON.parse(raw) as Record<string, unknown>;
    return { at: str(j["ts"]), level: String(j["level"] ?? "INFO"), title: String(j["title"] ?? j["message"] ?? "") };
  } catch {
    return { at: null, level: "INFO", title: raw.slice(0, 200) };
  }
}

export function readSystemHealth(config: ConsoleConfig): SystemHealthPayload {
  const cfg = readJson(config.paths.orchestratorConfig) ?? {};
  const wdCfg = (cfg["watchdog"] ?? {}) as Record<string, unknown>;
  const notifyCfg = (cfg["notify"] ?? {}) as Record<string, unknown>;
  const wd = readWatchdog(config);
  const sup = readSystemSupervisor(config);
  const lock = readLockStatus(config);
  const alertDaemon = readAlertDaemonHealth(config);
  const checks: SystemCheck[] = [];
  const f = (key: string) => wd.byKey.get(key);

  // Supervisor: the heartbeat rule, which is fresher than the watchdog's ten-minute tick.
  const hb = sup.heartbeat;
  checks.push({
    key: "supervisor",
    label: "Supervisor",
    level: !hb.present ? "critical" : hb.alive ? "ok" : "critical",
    value: hb.alive ? `running · pid ${String(hb.pid)}` : hb.present ? "not alive" : "no heartbeat",
    lines: [
      `heartbeat ${ageText(hb.ageSeconds)} ago`,
      hb.startedAt !== null ? `up ${ageText(ageOf(hb.startedAt))}` : "start time unknown",
      `${String(hb.jobs ?? "?")} jobs · ${String(hb.residentChildren ?? "?")} resident · ${String(hb.rssMb ?? "?")} MB`,
    ],
    source: `heartbeat rule (alive under ${String(SUPERVISOR_ALIVE_S)}s with its pid present)`,
  });

  // The registry's own record of each job: a hold, a backoff, a non-zero exit nobody asked for.
  const attention = sup.jobs.filter((j) => j.level === "warn" || j.level === "critical");
  checks.push({
    key: "jobs",
    label: "Jobs",
    level: attention.some((j) => j.level === "critical") ? "critical" : attention.length > 0 ? "warn" : "ok",
    value:
      attention.length === 0
        ? `${String(sup.jobs.length)} jobs · none need attention`
        : `${String(attention.length)} of ${String(sup.jobs.length)} need attention`,
    lines:
      attention.length === 0
        ? [`${String(sup.jobs.filter((j) => j.runningPid !== null).length)} running now`]
        : attention.slice(0, 3).map((j) => `${j.id}: ${j.state}${j.lastExitCode !== null && j.lastExitCode !== 0 ? ` (exit ${String(j.lastExitCode)})` : ""}`),
    source: "supervisor job registry",
  });

  const anchor = f("supervisor.anchor");
  checks.push({
    key: "anchor",
    label: "Anchor task",
    level: findingLevel(anchor?.status),
    value: anchor?.message ?? "no watchdog report",
    lines: [
      "the OS task that relaunches the supervisor every 2 min",
      `probe failures: ${String(sup.anchor.probeFailures ?? "n/r")}`,
    ],
    source: "watchdog supervisor.anchor",
  });

  const problems = wd.findings.filter((x) => x.status !== "OK");
  const wdAge = ageOf(wd.at);
  checks.push({
    key: "watchdog",
    label: "Watchdog",
    level: wd.overall === null ? "unknown" : findingLevel(wd.overall),
    value: wd.overall === null ? "no report" : `${wd.overall} · ${String(problems.length)} of ${String(wd.findings.length)} not OK`,
    lines: [
      `last ran ${ageText(wdAge)} ago, every ${String(wdCfg["interval_minutes"] ?? "?")} min`,
      ...problems.slice(0, 3).map((x) => `${x.status} ${x.title}`),
    ],
    source: "watchdog overall",
  });

  const streamer = f("streamer");
  const sf = streamerFreshness(config);
  checks.push({
    key: "streamer",
    label: "Streamer",
    level: findingLevel(streamer?.status),
    value: streamer?.message.split(" [")[0] ?? "no watchdog report",
    lines: [`last event ${ageText(sf.ageSeconds)} ago`, f("streamer.budget")?.message ?? ""].filter((l) => l !== ""),
    source: "watchdog streamer",
  });

  const liveOn = lock.modules.filter((m) => m.liveEnabled === true).map((m) => m.id);
  const armed = lock.modules.filter((m) => armedToday(config, m.id).armed).map((m) => m.id);
  checks.push({
    key: "live",
    label: "Live trading",
    level: lock.halted ? "critical" : armed.length > 0 ? "warn" : "ok",
    value: lock.halted ? "HALTED (flag present)" : armed.length > 0 ? `armed today: ${armed.join(", ")}` : "nothing armed today",
    lines: [
      `live enabled: ${liveOn.length > 0 ? liveOn.join(", ") : "none"}`,
      `halt flag ${lock.halted ? "present" : "clear"}`,
      alertDaemon !== null ? `alert daemon ${alertDaemon.state}` : "alert daemon not configured",
    ],
    source: "config gates, arm records, halt flag",
  });

  const tn = [f("trade_notify.saved"), f("trade_notify.errors")].filter((x) => x !== undefined);
  const worst = tn.map((x) => findingLevel(x.status)).sort((a, b) => rank(b) - rank(a))[0] ?? "unknown";
  const last = lastNotify(config);
  checks.push({
    key: "notify",
    label: "Notifications",
    level: worst,
    value: Array.isArray(notifyCfg["channels"]) ? (notifyCfg["channels"] as unknown[]).map(String).join(" · ") : "no channels",
    lines: [
      last !== null ? `last: ${last.level} ${last.title.slice(0, 60)}` : "nothing sent yet",
      ...tn.map((x) => `${x.title}: ${x.message}`),
    ],
    source: "watchdog trade_notify.*",
  });

  return {
    checkedAt: new Date().toISOString(),
    checks,
    watchdog: {
      at: wd.at,
      ageSeconds: wdAge,
      overall: wd.overall,
      intervalMinutes: num(wdCfg["interval_minutes"]),
      renotifyMinutes: num(wdCfg["renotify_minutes"]),
      // Problems first, worst first; the rest in the watchdog's own order.
      findings: [...wd.findings].sort((a, b) => rank(findingLevel(b.status)) - rank(findingLevel(a.status))),
    },
    live: {
      halted: lock.halted,
      modules: lock.modules.map((m) => {
        const a = armedToday(config, m.id);
        return { id: m.id, liveEnabled: m.liveEnabled, armedToday: a.armed, armDate: a.date };
      }),
      alertDaemon,
    },
    notify: {
      channels: Array.isArray(notifyCfg["channels"]) ? (notifyCfg["channels"] as unknown[]).map(String) : [],
      tradeChannels: Array.isArray(notifyCfg["trade_channels"]) ? (notifyCfg["trade_channels"] as unknown[]).map(String) : [],
      last,
      webhookNote: "the Discord webhook is held in the OS keyring, which the console cannot read; its sends show in notify.log",
    },
  };
}

function rank(l: SystemLevel): number {
  return l === "critical" ? 3 : l === "warn" ? 2 : l === "unknown" ? 1 : 0;
}
