/**
 * Where a supervised console's log goes.
 *
 * The supervisor spawns every job with stdout and stderr on DEVNULL — module loops are expected to
 * write their own log file, and until now the console did not, so under supervision its output went
 * nowhere. A read surface that the supervisor silently restarts, with nothing to read afterwards, is
 * not diagnosable. This writes `~/.cherrypick/logs/console/console.log`, the same logs-home
 * convention every Python module follows, and tees to stdout so running it by hand is unchanged.
 *
 * Rotation is by size rather than by day: the console is a long-lived process and its log has no
 * natural daily boundary, and an unbounded file on a machine that never restarts is the failure this
 * avoids. Mirrors the orchestrator's own `util.rotate_if_large`.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Writable } from "node:stream";

export const MAX_LOG_BYTES = 5 * 1024 * 1024;
export const KEEP_BACKUPS = 3;

export function logDir(): string {
  return path.join(os.homedir(), ".cherrypick", "logs", "console");
}

export function logPath(): string {
  return path.join(logDir(), "console.log");
}

/** Shift console.log → .1 → .2 → .3 and drop the oldest, when the live file has grown past the cap. */
export function rotateIfLarge(file: string, maxBytes = MAX_LOG_BYTES, keep = KEEP_BACKUPS): void {
  let size: number;
  try {
    size = fs.statSync(file).size;
  } catch {
    return; // no file yet
  }
  if (size < maxBytes) return;
  try {
    fs.rmSync(`${file}.${keep}`, { force: true });
    for (let i = keep - 1; i >= 1; i--) {
      if (fs.existsSync(`${file}.${i}`)) fs.renameSync(`${file}.${i}`, `${file}.${i + 1}`);
    }
    fs.renameSync(file, `${file}.1`);
  } catch {
    // A rotation that loses a race with another writer must not cost us the log line itself.
  }
}

/**
 * A stream Fastify's logger can write to: appends to the rotating log file, and also to stdout so a
 * human running `python run.py dashboard --serve` still sees output. Under the supervisor stdout is
 * DEVNULL, which makes the tee free.
 */
export function createLogStream(): Writable {
  fs.mkdirSync(logDir(), { recursive: true });
  const file = logPath();
  let sinceCheck = 0;

  return new Writable({
    write(chunk, _encoding, callback) {
      const line = chunk as Buffer;
      try {
        // Check size occasionally rather than per line — a stat on every log write is wasteful.
        sinceCheck += line.length;
        if (sinceCheck > 256 * 1024) {
          sinceCheck = 0;
          rotateIfLarge(file);
        }
        fs.appendFileSync(file, line);
      } catch {
        // Never let logging take the server down.
      }
      process.stdout.write(line);
      callback();
    },
  });
}
