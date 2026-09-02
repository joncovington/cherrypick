/**
 * The liveness file the supervisor watches.
 *
 * The console is a resident job, so "is the process alive" is the wrong question — a Node server
 * whose event loop has wedged stays alive indefinitely while answering nothing. This writes
 * `~/.cherrypick/state/console.heartbeat` on a timer, which only a healthy event loop can do, and
 * the supervisor treats a stale mtime as a reason to restart (its existing `silence_file` /
 * `silence_seconds` machinery, the same one flies' resident loop uses).
 *
 * Deliberately a file rather than an HTTP probe: the supervisor's reliability path is stdlib +
 * local files only, and a probe would have to grow its own timeout handling to catch the case this
 * catches for free.
 */
import fs from "node:fs";
import path from "node:path";

/** Write cadence. The supervisor's silence window is several times this, so one slow pass — a GC
 *  pause, a heavy SQL read on the main thread — never reads as a wedge. */
export const HEARTBEAT_INTERVAL_MS = 15_000;

export function heartbeatPath(cherrypickDir: string): string {
  return path.join(cherrypickDir, "state", "console.heartbeat");
}

export function writeHeartbeat(cherrypickDir: string, port: number): void {
  const file = heartbeatPath(cherrypickDir);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  // Only the mtime is read, so a torn write is harmless; the contents are for a human reading the
  // state dir. Written whole rather than appended so the file never grows.
  fs.writeFileSync(
    file,
    JSON.stringify({ ts: new Date().toISOString(), pid: process.pid, port }) + "\n",
    "utf-8",
  );
}

/** Start the heartbeat and return a stop function. Writes once immediately so a just-started
 *  console is visible before the first interval elapses. */
export function startHeartbeat(
  cherrypickDir: string,
  port: number,
  log: (msg: string) => void,
): () => void {
  let warned = false;
  const tick = () => {
    try {
      writeHeartbeat(cherrypickDir, port);
      warned = false;
    } catch (err) {
      // A heartbeat that throws must not take the server down with it — but a supervisor that
      // cannot see us will restart us, so say why once rather than on every tick.
      if (!warned) {
        log(`heartbeat write failed: ${(err as Error).message}`);
        warned = true;
      }
    }
  };
  tick();
  const timer = setInterval(tick, HEARTBEAT_INTERVAL_MS);
  return () => clearInterval(timer);
}
