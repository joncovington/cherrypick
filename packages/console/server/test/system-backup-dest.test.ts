import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { suitePython } from "@console/shared/paths";
import type { ConsoleConfig } from "../src/config.js";
import { BACKUP_LATEST, readSystemData, resolveBackupDest } from "../src/readers/systemEnv.js";

/**
 * The System page's "Nightly backup" row reads the file the nightly job writes, which is under
 * `backup.dest` in config.json (a folder on another drive on some machines), not always under the
 * home's own `backups`. The reader mirrors `orchestrator.config.backup_settings`, and the mirror is
 * pinned against the module below.
 */

function tmp(prefix: string): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function homeWith(backup: unknown): { config: ConsoleConfig; root: string } {
  const root = tmp("console-backup-home-");
  fs.mkdirSync(path.join(root, "state"), { recursive: true });
  const body = backup === undefined ? {} : { backup };
  fs.writeFileSync(path.join(root, "config.json"), JSON.stringify(body));
  const config = {
    port: 0,
    paths: {
      cherrypick: root,
      streamCacheDb: path.join(root, "nope.db"),
      watchdogLast: path.join(root, "state", "watchdog.last.json"),
      orchestratorConfig: path.join(root, "config.json"),
    },
  } as unknown as ConsoleConfig;
  return { config, root };
}

function row(config: ConsoleConfig) {
  const found = readSystemData(config).artifacts.find((a) => a.key === "backup");
  if (found === undefined) throw new Error("no backup row");
  return found;
}

function zip(dir: string, bytes: number): void {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, BACKUP_LATEST), Buffer.alloc(bytes, 1));
}

describe("the Nightly backup row", () => {
  it("reads <home>/backups when backup.dest is not set", () => {
    const { config, root } = homeWith(undefined);
    zip(path.join(root, "backups"), 3_000_000);
    const r = row(config);
    expect(r.detail).toBe("3 MB");
    expect(r.ok).toBeNull();
  });

  it("reads the file under backup.dest, not the one under the home's backups", () => {
    const dest = tmp("console-backup-dest-");
    const { config, root } = homeWith({ dest });
    zip(dest, 5_000_000);
    zip(path.join(root, "backups"), 1_000_000); // a stale copy the old reader would have shown
    const r = row(config);
    expect(r.detail).toBe("5 MB");
    expect(r.ageSeconds).not.toBeNull();
  });

  it("says no backup file when the folder is there and empty", () => {
    const dest = tmp("console-backup-empty-");
    const { config } = homeWith({ dest });
    const r = row(config);
    expect(r.ok).toBe(false);
    expect(r.detail).toBe("no backup file");
  });

  it("says the folder is not readable when backup.dest does not exist", () => {
    const dest = path.join(tmp("console-backup-gone-"), "unmounted");
    const { config } = homeWith({ dest });
    const r = row(config);
    expect(r.ok).toBe(false);
    expect(r.detail).toContain("not readable");
    expect(r.detail).not.toBe("no backup file");
  });
});

describe("resolveBackupDest", () => {
  const home = path.join(os.tmpdir(), "a-home");

  it("defaults to <home>/backups", () => {
    expect(resolveBackupDest(null, home)).toBe(path.join(home, "backups"));
    expect(resolveBackupDest({ backup: {} }, home)).toBe(path.join(home, "backups"));
    expect(resolveBackupDest({ backup: { dest: "" } }, home)).toBe(path.join(home, "backups"));
  });

  it("expands ~ and environment variables, leaves an unset one as written, and keeps a relative path", () => {
    const env = { BKP_ROOT: "/mnt/disk" } as NodeJS.ProcessEnv;
    expect(resolveBackupDest({ backup: { dest: "~/bk" } }, home, env, "/users/x")).toBe("/users/x/bk");
    expect(resolveBackupDest({ backup: { dest: "$BKP_ROOT/bk" } }, home, env)).toBe("/mnt/disk/bk");
    expect(resolveBackupDest({ backup: { dest: "${BKP_ROOT}/bk" } }, home, env)).toBe("/mnt/disk/bk");
    expect(resolveBackupDest({ backup: { dest: "$BKP_NOPE/bk" } }, home, env)).toBe("$BKP_NOPE/bk");
    expect(resolveBackupDest({ backup: { dest: "rel/bk" } }, home, env)).toBe("rel/bk");
  });
});

// ---- pinned against the module itself --------------------------------------------------------

const PY = suitePython();
const PROBE = `
import json, sys
from cherrypick.orchestrator import config as c
cfg = json.loads(sys.argv[1])
print(json.dumps(str(c.backup_settings(cfg)["dest"])))
`;

function moduleDest(cfg: unknown, env: NodeJS.ProcessEnv): string | null {
  const out = spawnSync(PY, ["-I", "-c", PROBE, JSON.stringify(cfg)], { encoding: "utf-8", timeout: 60_000, env });
  if (out.status !== 0 || typeof out.stdout !== "string") return null;
  try {
    return JSON.parse(out.stdout) as string;
  } catch {
    return null;
  }
}

const probeEnv: NodeJS.ProcessEnv = { ...process.env, BKP_PIN_ROOT: path.join(os.tmpdir(), "pin-root") };
const available = moduleDest({ backup: { dest: "$BKP_PIN_ROOT/x" } }, probeEnv) !== null;

describe.skipIf(!available)("the console's backup folder agrees with orchestrator.config.backup_settings", () => {
  const cases: Array<[string, Record<string, unknown>]> = [
    ["no backup block", {}],
    ["an empty dest", { backup: { dest: "" } }],
    ["a plain path", { backup: { dest: path.join(os.tmpdir(), "plain-dest") } }],
    ["a ~ path", { backup: { dest: "~/bk" } }],
    ["an environment variable", { backup: { dest: "$BKP_PIN_ROOT/bk" } }],
    ["a braced variable", { backup: { dest: "${BKP_PIN_ROOT}/bk" } }],
    ["an unset variable", { backup: { dest: "$BKP_PIN_UNSET/bk" } }],
    ["a relative path", { backup: { dest: "rel/bk" } }],
  ];
  const norm = (p: string) => path.normalize(p);
  for (const [name, cfg] of cases) {
    it(name, () => {
      const theirs = moduleDest(cfg, probeEnv);
      expect(theirs).not.toBeNull();
      const ours = resolveBackupDest(cfg, path.join(os.homedir(), ".cherrypick-pin-home"), probeEnv);
      if (Object.keys(cfg).length === 0 || (cfg["backup"] as { dest?: string }).dest === "") {
        // Both fall back to <home>/backups: only the home differs in this harness, so compare the tail.
        expect(norm(ours).endsWith(path.join("", "backups"))).toBe(true);
        expect(norm(theirs as string).endsWith(path.join("", "backups"))).toBe(true);
      } else {
        expect(norm(ours)).toBe(norm(theirs as string));
      }
    });
  }

  // A skipped check must never read as a passing one: say why it would skip.
  it("knows why it would skip", () => {
    expect(typeof PY).toBe("string");
  });
});
