/**
 * The System page's data and environment tabs.
 *
 * Two things here go beyond reading a file, both read-only and both cached so a page poll never
 * pays for them: `git` (what the checkout holds, and which running processes predate their own
 * package's latest commit) and one `python -c` (the interpreter and package versions the suite
 * runs on). Store sizes walk ~16 GB of Dolt clones, so they are measured in the background and
 * served with the time they were measured.
 */

import { execFile } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Database from "better-sqlite3";
import type {
  SystemArtifact,
  SystemDataPayload,
  SystemEnvironmentPayload,
  SystemProcessAge,
  SystemStore,
} from "@console/shared";
import { suitePython } from "@console/shared";
import { REPO_ROOT, type ConsoleConfig } from "../config.js";
import { listReaderFailures, readJson } from "./db.js";

function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function ageOf(iso: string | null): number | null {
  if (iso === null) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Math.max(0, (Date.now() - t) / 1000);
}

function mtimeIso(p: string): string | null {
  try {
    return new Date(fs.statSync(p).mtimeMs).toISOString();
  } catch {
    return null;
  }
}

// --------------------------------------------------------------------------- data

function artifact(key: string, label: string, at: string | null, ok: boolean | null, detail: string | null): SystemArtifact {
  return { key, label, at, ageSeconds: ageOf(at), ok, detail };
}

function readArtifacts(config: ConsoleConfig): SystemArtifact[] {
  const home = config.paths.cherrypick;
  const st = (n: string) => path.join(home, "state", n);
  const out: SystemArtifact[] = [];

  const fut = readJson(st("futures_contracts.json"));
  const contracts = (fut?.["contracts"] ?? {}) as Record<string, unknown>;
  out.push(
    artifact("futures", "Futures contract map", str(fut?.["refreshed_at"]), fut !== null, `${String(Object.keys(contracts).length)} products`),
  );

  const dolt = readJson(st("dolt_data.json"));
  const dbs = (dolt?.["databases"] ?? {}) as Record<string, Record<string, unknown>>;
  const failed = Object.entries(dbs).filter(([, d]) => d["ok"] !== true).map(([k]) => k);
  out.push(
    artifact(
      "dolt",
      "Dolt clones (earnings · options · stocks)",
      str(dolt?.["refreshed_at"]),
      dolt === null ? null : failed.length === 0,
      dolt === null
        ? "never pulled"
        : `${failed.length === 0 ? "all pulled" : `failed: ${failed.join(", ")}`} · earnings calendar to ${String(dolt["earnings_calendar_max_date"] ?? "?")}`,
    ),
  );

  const morning = readJson(st("morning.last.json"));
  out.push(
    artifact(
      "morning",
      "Morning pack",
      mtimeIso(st("morning.last.json")),
      morning === null ? null : morning["ok"] === true,
      morning === null ? "never run" : `session ${String(morning["session"] ?? "?")} · phase ${String(morning["phase"] ?? "?")}${morning["error"] ? ` · ${String(morning["error"])}` : ""}`,
    ),
  );

  const review = readJson(st("review.last.json"));
  out.push(
    artifact(
      "review",
      "End-of-day review",
      mtimeIso(st("review.last.json")),
      review === null ? null : review["ok"] === true,
      review === null ? "never run" : `session ${String(review["session"] ?? "?")} · ${String(review["status"] ?? review["pass"] ?? "?")}${review["error"] ? ` · ${String(review["error"])}` : ""}`,
    ),
  );

  try {
    const db = new Database(path.join(home, "data", "technicals", "eod.db"), { readonly: true, fileMustExist: true });
    try {
      const row = db.prepare("SELECT landed_at, through, symbols, bars FROM landings ORDER BY landed_at DESC LIMIT 1").get() as
        | { landed_at: number; through: string | null; symbols: number | null; bars: number | null }
        | undefined;
      out.push(
        artifact(
          "technicals",
          "Technicals landing",
          row ? new Date(row.landed_at * 1000).toISOString() : null,
          row ? true : null,
          row ? `through ${String(row.through)} · ${String(row.symbols)} symbols · ${String(row.bars)} bars` : "no landing",
        ),
      );
    } finally {
      db.close();
    }
  } catch {
    out.push(artifact("technicals", "Technicals landing", null, null, "no store"));
  }

  const gexBeat = path.join(home, "data", "gex", "recorder.heartbeat");
  out.push(artifact("gex", "GEX recorder heartbeat", mtimeIso(gexBeat), null, null));

  const backup = path.join(home, "backups", "cherrypick-backup.zip");
  let backupSize: number | null = null;
  try {
    backupSize = fs.statSync(backup).size;
  } catch {
    backupSize = null;
  }
  out.push(
    artifact("backup", "Nightly backup", mtimeIso(backup), backupSize === null ? false : null, backupSize !== null ? `${(backupSize / 1e6).toFixed(0)} MB` : "no backup file"),
  );

  const guards = readJson(st("guard-mutants.last.json"));
  const mutants = Array.isArray(guards?.["mutants"]) ? (guards["mutants"] as Array<Record<string, unknown>>) : [];
  out.push(
    artifact(
      "guards",
      "Guard mutants (each guard shown to fail)",
      mtimeIso(st("guard-mutants.last.json")),
      guards === null ? null : guards["ok"] === true,
      guards === null ? "never run" : `${String(mutants.length)} mutants`,
    ),
  );
  return out;
}

const STORE_TTL_MS = 15 * 60_000;
let storeCache: { at: number; stores: SystemStore[] } | null = null;
let storeWalk: Promise<void> | null = null;

async function dirSize(p: string): Promise<number> {
  let total = 0;
  let entries: fs.Dirent[];
  try {
    entries = await fs.promises.readdir(p, { withFileTypes: true });
  } catch {
    return 0;
  }
  for (const e of entries) {
    const full = path.join(p, e.name);
    if (e.isDirectory()) total += await dirSize(full);
    else if (e.isFile()) {
      try {
        total += (await fs.promises.stat(full)).size;
      } catch {
        /* a file removed mid-walk */
      }
    }
  }
  return total;
}

async function measureStores(home: string): Promise<SystemStore[]> {
  const data = path.join(home, "data");
  let dirs: string[] = [];
  try {
    dirs = (await fs.promises.readdir(data, { withFileTypes: true })).filter((d) => d.isDirectory()).map((d) => d.name);
  } catch {
    dirs = [];
  }
  const targets: Array<[string, string]> = [
    ...dirs.map((d): [string, string] => [`data/${d}`, path.join(data, d)]),
    ["logs", path.join(home, "logs")],
    ["archive", path.join(home, "archive")],
    ["backups", path.join(home, "backups")],
  ];
  const stores: SystemStore[] = [];
  for (const [label, p] of targets) stores.push({ label, path: label, sizeBytes: await dirSize(p) });
  return stores.sort((a, b) => (b.sizeBytes ?? 0) - (a.sizeBytes ?? 0));
}

/** Cached sizes, refreshed in the background once stale; null until the first walk lands. */
function storeSizes(home: string): { stores: SystemStore[]; at: string | null } {
  if ((storeCache === null || Date.now() - storeCache.at > STORE_TTL_MS) && storeWalk === null) {
    storeWalk = measureStores(home)
      .then((stores) => {
        storeCache = { at: Date.now(), stores };
      })
      .catch(() => undefined)
      .finally(() => {
        storeWalk = null;
      });
  }
  return storeCache === null ? { stores: [], at: null } : { stores: storeCache.stores, at: new Date(storeCache.at).toISOString() };
}

function readStream(config: ConsoleConfig): SystemDataPayload["stream"] {
  const p = config.paths.streamCacheDb;
  if (!fs.existsSync(p)) return null;
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    const s = db.prepare("SELECT pid, connected_since, last_event_at, subscribed_symbols, reconnect_count FROM stream_status WHERE id = 1").get() as
      | Record<string, unknown>
      | undefined;
    const tables: NonNullable<SystemDataPayload["stream"]>["tables"] = [];
    for (const t of ["stream_quotes", "stream_trades", "stream_greeks", "stream_chain", "stream_summary", "stream_oi"]) {
      try {
        const r = db.prepare(`SELECT COUNT(*) AS n, MAX(updated_at) AS newest FROM ${t}`).get() as { n: number; newest: number | null };
        tables.push({ table: t, rows: r.n, newestAgeSeconds: r.newest !== null ? Math.max(0, Date.now() / 1000 - r.newest) : null });
      } catch {
        /* a table this producer version does not have */
      }
    }
    const last = s?.["last_event_at"];
    const lastMs = typeof last === "number" ? last * 1000 : typeof last === "string" ? Date.parse(last) : NaN;
    return {
      pid: typeof s?.["pid"] === "number" ? s["pid"] : null,
      connectedSince: str(s?.["connected_since"]),
      lastEventAgeSeconds: Number.isNaN(lastMs) ? null : Math.max(0, (Date.now() - lastMs) / 1000),
      subscribedSymbols: typeof s?.["subscribed_symbols"] === "number" ? s["subscribed_symbols"] : null,
      reconnects: typeof s?.["reconnect_count"] === "number" ? s["reconnect_count"] : null,
      tables,
    };
  } catch {
    return null;
  } finally {
    db?.close();
  }
}

export function readSystemData(config: ConsoleConfig): SystemDataPayload {
  const home = config.paths.cherrypick;
  const sizes = storeSizes(home);
  let disk: SystemDataPayload["disk"] = null;
  try {
    const s = fs.statfsSync(home);
    disk = { path: home, freeBytes: s.bavail * s.bsize, totalBytes: s.blocks * s.bsize };
  } catch {
    disk = null;
  }
  return {
    artifacts: readArtifacts(config),
    stores: sizes.stores,
    storesMeasuredAt: sizes.at,
    disk,
    readerFailures: listReaderFailures(),
    stream: readStream(config),
  };
}

// --------------------------------------------------------------------------- environment

function run(cmd: string, args: string[], timeoutMs = 10_000): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, { cwd: REPO_ROOT, timeout: timeoutMs, windowsHide: true, maxBuffer: 4 * 1024 * 1024 }, (err, stdout) => {
      if (err) reject(err);
      else resolve(stdout.trim());
    });
  });
}

const PY_PACKAGES = ["tastytrade", "pydantic", "httpx", "websockets", "keyring", "numpy", "pandas"];
const PY_SCRIPT = [
  "import json, sys, importlib.metadata as m",
  "def v(n):",
  "    try: return m.version(n)",
  "    except Exception: return None",
  `print(json.dumps({"version": sys.version.split()[0], "executable": sys.executable, "packages": {n: v(n) for n in ${JSON.stringify(PY_PACKAGES)}}}))`,
].join("\n");

type Python = SystemEnvironmentPayload["runtime"]["python"];
let pythonCache: { at: number; value: Python } | null = null;

async function pythonInfo(): Promise<Python> {
  if (pythonCache !== null && Date.now() - pythonCache.at < 3_600_000) return pythonCache.value;
  let value: Python;
  try {
    const j = JSON.parse(await run(suitePython(), ["-c", PY_SCRIPT])) as { version: string; executable: string; packages: Record<string, string | null> };
    value = { version: j.version, executable: j.executable, packages: j.packages, error: null };
  } catch (err) {
    value = { version: null, executable: null, packages: {}, error: err instanceof Error ? err.message.split("\n")[0] ?? "failed" : "failed" };
  }
  pythonCache = { at: Date.now(), value };
  return value;
}

type Git = SystemEnvironmentPayload["git"];
let gitCache: { at: number; value: Git; processes: SystemProcessAge[] } | null = null;

async function lastCommitTouching(paths: string[]): Promise<{ at: string; sha: string } | null> {
  try {
    const out = await run("git", ["log", "-1", "--format=%H%x09%cI", "--", ...paths]);
    const [sha, at] = out.split("\t");
    return sha && at ? { sha: sha.slice(0, 8), at } : null;
  } catch {
    return null;
  }
}

const SOURCE_FILE = /\.(py|ts|tsx|js|mjs)$/;
const TEST_PATH = /(^|\/)(tests?|__tests__)\//;

/** The newest modification time among a package set's tracked source files, tests excluded. */
async function newestSourceChange(paths: string[]): Promise<{ at: string; file: string } | null> {
  let files: string[];
  try {
    files = (await run("git", ["ls-files", "--", ...paths]))
      .split("\n")
      .filter((f) => SOURCE_FILE.test(f) && !TEST_PATH.test(f));
  } catch {
    return null;
  }
  let best: { ms: number; file: string } | null = null;
  for (const f of files) {
    try {
      const ms = fs.statSync(path.join(REPO_ROOT, f)).mtimeMs;
      if (best === null || ms > best.ms) best = { ms, file: f };
    } catch {
      /* deleted in the working tree */
    }
  }
  return best === null ? null : { at: new Date(best.ms).toISOString(), file: best.file };
}

/** Which running processes loaded code that has changed on disk since. */
async function processAges(config: ConsoleConfig, consoleBuiltAt: string | null): Promise<SystemProcessAge[]> {
  const home = config.paths.cherrypick;
  const reg = readJson(path.join(home, "state", "supervisor-jobs.json"));
  const sup = readJson(path.join(home, "state", "supervisor.last.json"));
  const launch = (id: string) => readJson(path.join(home, "state", `service-${id}.launch.json`));
  const epoch = (v: unknown) => (typeof v === "number" ? new Date(v * 1000).toISOString() : null);

  const specs: Array<{ name: string; startedAt: string | null; packages: string[] }> = [
    { name: "console", startedAt: new Date(Date.now() - process.uptime() * 1000).toISOString(), packages: ["packages/console"] },
    { name: "supervisor", startedAt: str(sup?.["started_at"]), packages: ["packages/orchestrator", "packages/core"] },
    { name: "streamer", startedAt: epoch(launch("streamer")?.["stamped_at"]), packages: ["packages/streamer", "packages/core"] },
    { name: "gex-recorder", startedAt: epoch(launch("gex-recorder")?.["stamped_at"]), packages: ["packages/gex", "packages/core"] },
  ];
  // Every job running right now (a resident loop, or an interval job mid-run) is a process too.
  for (const [id, row] of Object.entries((reg?.["jobs"] ?? {}) as Record<string, Record<string, unknown>>)) {
    if (typeof row["running_pid"] !== "number" || id === "console") continue;
    const pkg = id.split("-")[0] ?? id;
    const pkgs = fs.existsSync(path.join(REPO_ROOT, "packages", pkg)) ? [`packages/${pkg}`, "packages/core"] : ["packages/orchestrator", "packages/core"];
    specs.push({ name: id, startedAt: epoch(row["pid_started_at"]), packages: pkgs });
  }

  // One walk per distinct package set: the jobs share a handful.
  const changes = new Map<string, Promise<{ at: string; file: string } | null>>();
  const out: SystemProcessAge[] = [];
  for (const s of specs) {
    const key = s.packages.join("|");
    if (!changes.has(key)) changes.set(key, newestSourceChange(s.packages));
    const [commit, change] = await Promise.all([lastCommitTouching(s.packages), changes.get(key)!]);
    // The console runs its BUILD: the code it holds is as of the build, provided it started after
    // that build. Started before the build, it holds an older one.
    const loadedAt =
      s.name === "console" && consoleBuiltAt !== null && s.startedAt !== null
        ? Date.parse(consoleBuiltAt) <= Date.parse(s.startedAt)
          ? consoleBuiltAt
          : null
        : s.startedAt;
    out.push({
      name: s.name,
      startedAt: s.startedAt,
      packages: s.packages,
      latestCommitAt: commit?.at ?? null,
      latestCommit: commit?.sha ?? null,
      codeChangedAt: change?.at ?? null,
      codeChangedFile: change?.file ?? null,
      stale: change === null || s.startedAt === null ? null : loadedAt === null ? true : Date.parse(loadedAt) < Date.parse(change.at),
    });
  }
  return out;
}

async function gitInfo(config: ConsoleConfig, consoleBuiltAt: string | null): Promise<{ git: Git; processes: SystemProcessAge[] }> {
  if (gitCache !== null && Date.now() - gitCache.at < 60_000) return { git: gitCache.value, processes: gitCache.processes };
  let git: Git;
  try {
    const branch = await run("git", ["rev-parse", "--abbrev-ref", "HEAD"]);
    const [head, headAt, ...subject] = (await run("git", ["log", "-1", "--format=%H%x09%cI%x09%s"])).split("\t");
    const dirty = (await run("git", ["status", "--porcelain"])).split("\n").filter((l) => l.trim() !== "").length;
    let upstream: string | null = null;
    let ahead: number | null = null;
    let behind: number | null = null;
    try {
      upstream = await run("git", ["rev-parse", "--abbrev-ref", "@{upstream}"]);
      const [a, b] = (await run("git", ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"])).split(/\s+/);
      ahead = Number(a);
      behind = Number(b);
    } catch {
      upstream = null;
    }
    git = {
      branch,
      head: head?.slice(0, 8) ?? null,
      headSubject: subject.join("\t") || null,
      headAt: headAt ?? null,
      dirtyFiles: dirty,
      upstream,
      ahead,
      behind,
      // Ahead/behind is against the last fetch, never a live remote: this asks no network.
      lastFetchAt: mtimeIso(path.join(REPO_ROOT, ".git", "FETCH_HEAD")),
      error: null,
    };
  } catch (err) {
    git = {
      branch: null, head: null, headSubject: null, headAt: null, dirtyFiles: null,
      upstream: null, ahead: null, behind: null, lastFetchAt: null,
      error: err instanceof Error ? err.message.split("\n")[0] ?? "git failed" : "git failed",
    };
  }
  const processes = git.error === null ? await processAges(config, consoleBuiltAt) : [];
  gitCache = { at: Date.now(), value: git, processes };
  return { git, processes };
}

/** Exchange event to cache write, over live futures prints: clock skew + latency + the flush. */
function feedLag(config: ConsoleConfig): SystemEnvironmentPayload["feedLag"] {
  let db: Database.Database | null = null;
  try {
    db = new Database(config.paths.streamCacheDb, { readonly: true, fileMustExist: true });
    // Futures only: an option row keeps its LAST trade's event time while being rewritten, so
    // its "lag" is hours. A future prints continuously, and nearly the whole day.
    const rows = db
      .prepare("SELECT symbol, updated_at, event_at FROM stream_trades WHERE symbol LIKE '/%' AND event_at IS NOT NULL AND updated_at > ?")
      .all(Date.now() / 1000 - 300) as Array<{ symbol: string; updated_at: number; event_at: number }>;
    const lags = rows.map((r) => (r.updated_at - r.event_at) * 1000).filter((l) => l > -60_000 && l < 60_000).sort((a, b) => a - b);
    if (lags.length === 0) return { medianMs: null, samples: 0, symbol: null };
    return { medianMs: Math.round(lags[Math.floor(lags.length / 2)]!), samples: lags.length, symbol: "futures" };
  } catch {
    return null;
  } finally {
    db?.close();
  }
}

function consoleBuildTime(): string | null {
  // dist/readers/systemEnv.js → dist/index.js; under the test runner (src/*.ts) there is no build.
  const here = path.dirname(fileURLToPath(import.meta.url));
  return mtimeIso(path.resolve(here, "..", "index.js"));
}

export async function readSystemEnvironment(config: ConsoleConfig): Promise<SystemEnvironmentPayload> {
  const builtAt = consoleBuildTime();
  const [python, { git, processes }] = await Promise.all([pythonInfo(), gitInfo(config, builtAt)]);
  return {
    machine: {
      hostname: os.hostname(),
      platform: `${os.type()} ${os.arch()}`,
      release: os.release(),
      uptimeSeconds: os.uptime(),
      totalMemBytes: os.totalmem(),
      freeMemBytes: os.freemem(),
      cpus: os.cpus().length,
    },
    runtime: {
      node: process.version,
      consoleStartedAt: new Date(Date.now() - process.uptime() * 1000).toISOString(),
      consoleBuiltAt: builtAt,
      python,
    },
    git,
    processes,
    feedLag: feedLag(config),
  };
}
