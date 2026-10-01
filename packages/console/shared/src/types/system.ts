// The System page (2026-10-01): the suite's own health, read from the files the suite writes.
//
// Nothing here is re-derived where the suite already decided it. A tile's level is the watchdog's
// finding for that subsystem, or the suite's own rule where one exists (the supervisor heartbeat:
// alive when under 90s old with its pid present, `orchestrator/supersnap.py`). The console adds
// facts beside those verdicts, never a second opinion of them.

import type { AlertDaemonHealth } from "./status.js";

export type SystemLevel = "ok" | "warn" | "critical" | "unknown";

/** One tile on the health tab. */
export interface SystemCheck {
  key: string;
  label: string;
  level: SystemLevel;
  /** The headline fact: "running · pid 36412". */
  value: string;
  /** Supporting facts, one per line. */
  lines: string[];
  /** Where the level came from: "watchdog supervisor.alive", "heartbeat rule", … */
  source: string;
}

export interface SystemFinding {
  key: string;
  status: string;
  title: string;
  message: string;
  /** When the watchdog first saw this status (its renotify state), if it has not been OK since. */
  since: string | null;
}

export interface SystemHealthPayload {
  checkedAt: string;
  checks: SystemCheck[];
  watchdog: {
    at: string | null;
    ageSeconds: number | null;
    overall: string | null;
    intervalMinutes: number | null;
    renotifyMinutes: number | null;
    findings: SystemFinding[];
  };
  live: {
    halted: boolean;
    modules: Array<{ id: string; liveEnabled: boolean | null; armedToday: boolean; armDate: string | null }>;
    alertDaemon: AlertDaemonHealth | null;
  };
  notify: {
    channels: string[];
    tradeChannels: string[];
    /** The newest line of `logs/notify.log`. */
    last: { at: string | null; level: string; title: string } | null;
    /** The webhook lives in the OS keyring, which Node cannot address; said, never guessed. */
    webhookNote: string;
  };
}

export interface SupervisorJob {
  id: string;
  kind: string | null;
  enabled: boolean;
  enabledReason: string | null;
  schedule: string | null;
  /** held / running / backoff / failing / disabled / outside window / idle — from the registry row. */
  state: string;
  level: SystemLevel;
  runningPid: number | null;
  pidStartedAt: string | null;
  lastStart: string | null;
  lastExitCode: number | null;
  lastExitAt: string | null;
  /** The last exit was a requested stop or restart, not a failure. */
  lastExitRequested: boolean;
  consecutiveFailures: number;
  backoffUntil: string | null;
  /** Interval jobs only: the registry does not record a daily job's next fire. */
  nextRun: string | null;
  lastFireDay: string | null;
  missed: string | null;
  lastError: string | null;
  lastRestart: { at: string | null; result: string | null } | null;
  /** Relative to the runtime home: `logs/jobs/<id>.stderr.log`, when it exists. */
  stderrLog: string | null;
}

export interface SupervisorDaemon {
  id: string;
  pid: number | null;
  alive: boolean | null;
  launchedAt: string | null;
  heartbeatAgeSeconds: number | null;
  watchdog: { status: string; note: string } | null;
}

export interface SystemSupervisorPayload {
  heartbeat: {
    present: boolean;
    alive: boolean;
    ageSeconds: number | null;
    pid: number | null;
    pidAlive: boolean | null;
    startedAt: string | null;
    loopSeq: number | null;
    jobs: number | null;
    residentChildren: number | null;
    rssMb: number | null;
  };
  registry: { writtenAt: string | null; ageSeconds: number | null; deriveErrors: Record<string, string> };
  jobs: SupervisorJob[];
  holds: Array<{ name: string; by: string | null; at: string | null }>;
  restartRequests: Array<{ job: string; requestedAt: string | null; by: string | null }>;
  daemons: SupervisorDaemon[];
  anchor: { status: string | null; message: string | null; probeFailures: number | null };
}

export interface SystemModuleRow {
  id: string;
  enabled: boolean;
  kind: string | null;
  liveEnabled: boolean | null;
  armedToday: boolean;
  jobs: Array<{ id: string; state: string; level: SystemLevel }>;
  heartbeatAgeSeconds: number | null;
  ledger: { path: string; sizeBytes: number; ageSeconds: number } | null;
  streamRequest: { symbols: number; legs: number; ageSeconds: number } | null;
  /** The watchdog's `<module>.fresh` and `<module>.task` findings. */
  findings: Array<{ key: string; status: string; message: string }>;
}

export interface SystemModulesPayload {
  modules: SystemModuleRow[];
}

export interface SystemArtifact {
  key: string;
  label: string;
  at: string | null;
  ageSeconds: number | null;
  ok: boolean | null;
  detail: string | null;
}

export interface SystemStore {
  label: string;
  path: string;
  sizeBytes: number | null;
}

export interface SystemDataPayload {
  artifacts: SystemArtifact[];
  stores: SystemStore[];
  /** When the sizes were measured: a walk of ~16 GB is cached, not repeated per request. */
  storesMeasuredAt: string | null;
  disk: { path: string; freeBytes: number; totalBytes: number } | null;
  readerFailures: Array<{ path: string; error: string; count: number; lastAt: string }>;
  stream: {
    pid: number | null;
    connectedSince: string | null;
    lastEventAgeSeconds: number | null;
    subscribedSymbols: number | null;
    reconnects: number | null;
    tables: Array<{ table: string; rows: number; newestAgeSeconds: number | null }>;
  } | null;
}

export interface SystemProcessAge {
  name: string;
  startedAt: string | null;
  /** The packages whose code it runs. */
  packages: string[];
  /** The newest commit touching any of them. */
  latestCommitAt: string | null;
  latestCommit: string | null;
  /** Started before that commit: it is running older code until restarted. */
  stale: boolean | null;
}

export interface SystemEnvironmentPayload {
  machine: {
    hostname: string;
    platform: string;
    release: string;
    uptimeSeconds: number;
    totalMemBytes: number;
    freeMemBytes: number;
    cpus: number;
  };
  runtime: {
    node: string;
    consoleStartedAt: string;
    consoleBuiltAt: string | null;
    python: { version: string | null; executable: string | null; packages: Record<string, string | null>; error: string | null };
  };
  git: {
    branch: string | null;
    head: string | null;
    headSubject: string | null;
    headAt: string | null;
    dirtyFiles: number | null;
    upstream: string | null;
    ahead: number | null;
    behind: number | null;
    lastFetchAt: string | null;
    error: string | null;
  };
  processes: SystemProcessAge[];
  /** Local receive time minus exchange event time over recent trades: clock skew plus latency. */
  feedLag: { medianMs: number | null; samples: number; symbol: string | null } | null;
}

export interface SystemLogSource {
  id: string;
  path: string;
  exists: boolean;
}
