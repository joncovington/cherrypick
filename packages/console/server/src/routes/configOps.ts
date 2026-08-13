import type { FastifyInstance, FastifyReply } from "fastify";
import type { ConsoleConfig } from "../config.js";
import { callConfigCli, statusForCode, type BridgeOk, type BridgeResult } from "../services/configBridge.js";
import { readLockStatus } from "../services/liveLock.js";
import { getPrefs, setPref } from "../store/consoleDb.js";

/**
 * The Config page's server half.
 *
 * Two different kinds of write live here and they are deliberately not alike:
 *
 *  - **Suite config** is staged in the browser and saved a section at a time, and every save goes
 *    out through the orchestrator's own editor (see `services/configBridge.ts`). This package never
 *    writes another package's config file itself, and the guarded live pointers are unreachable
 *    from here in either direction because that editor refuses them.
 *  - **The halt flag** is immediate, because a stop that takes two steps is a stop that arrives
 *    late. The friction is asymmetric on purpose: setting it is one click, clearing it needs the
 *    typed confirmation below — the same shape as the suite's other live rituals, where the
 *    de-risking direction is always the cheap one.
 *
 * Gating is the mutating-surface posture from `security.ts` (loopback Host, CSRF, JSON) — the same
 * bar the orchestrator's settings server sets. Notably NOT the broker credential scope: that
 * describes what a token may do at the broker, and a config file is not the broker.
 */

/** The literal that must be typed to clear the halt. Checked server-side, not just in the UI. */
export const RESUME_CONFIRMATION = "RESUME LIVE";

const TARGETS = ["orchestrator", "meic", "flies", "gex", "earnings", "streamer", "meic-risk"] as const;

/**
 * Pass a refusal through with its meaning intact: a guarded pointer, a stale file and a validation
 * error are three different things for the page to say, and collapsing them to "save failed" would
 * make the one that matters most — the guarded one — look like a bug.
 */
function sendBridgeFailure(reply: FastifyReply, result: BridgeResult): BridgeOk | null {
  if (result.ok) return result;
  void reply.code(statusForCode(result.code)).send({
    error: result.error,
    code: result.code,
    ...(result.pointer !== undefined ? { pointer: result.pointer } : {}),
    ...(result.issues !== undefined ? { issues: result.issues } : {}),
  });
  return null;
}

export function registerConfigRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  /**
   * Every editable target in one read. Fetched on mount and after a save, never polled — each
   * target is a subprocess. Configs hold no secrets (they live in the keyring, suite-wide), so the
   * documents travel whole and the client decides which fields it is willing to show.
   */
  app.get("/api/config/model", async () => {
    const targets: Record<string, unknown> = {};
    for (const id of TARGETS) {
      const result = callConfigCli({ op: "load", target: id });
      targets[id] = result.ok
        ? {
            exists: result["exists"] === true,
            portable: result["portable"] ?? null,
            doc: result["doc"] ?? null,
            mtime: result["mtime"] ?? null,
            guarded: result["guarded"] ?? [],
            issues: result["issues"] ?? [],
          }
        : { exists: false, doc: null, mtime: null, guarded: [], issues: [], error: result.error };
    }
    return { targets };
  });

  /** The lock hero's read: file-only, cheap enough to poll every few seconds. */
  app.get("/api/config/lock", async () => readLockStatus(config));

  app.post("/api/config/lock", async (req, reply) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const present = body["present"];
    if (typeof present !== "boolean") {
      return reply.code(400).send({ error: "present (boolean) required" });
    }
    // Clearing the halt is the direction that lets live entries resume, so it carries the same
    // shape of deliberate confirmation the suite's arming rituals use. Setting it never does.
    if (present === false && body["confirm"] !== RESUME_CONFIRMATION) {
      return reply.code(400).send({
        error: `clearing the halt needs confirm: "${RESUME_CONFIRMATION}"`,
        code: "confirm_required",
      });
    }
    if (sendBridgeFailure(reply, callConfigCli({ op: "set_halt", present })) === null) return reply;
    app.log.info(`halt flag ${present ? "set" : "cleared"} from the console config page`);
    return readLockStatus(config);
  });

  /**
   * One section's staged edits, applied as a single atomic write with one backup. `expectedMtime`
   * carries the version the client staged against; a mismatch comes back 409 rather than
   * overwriting an edit someone made in the meantime.
   */
  app.post("/api/config/save", async (req, reply) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const target = body["target"];
    const rawEdits = body["edits"];
    if (typeof target !== "string" || !(TARGETS as readonly string[]).includes(target)) {
      return reply.code(400).send({ error: "target must be one of: " + TARGETS.join(", ") });
    }
    if (!Array.isArray(rawEdits) || rawEdits.length === 0) {
      return reply.code(400).send({ error: "edits [{pointer, value}] required" });
    }
    const edits: Array<{ pointer: string; value: unknown }> = [];
    for (const raw of rawEdits) {
      const e = raw as Record<string, unknown>;
      if (typeof e["pointer"] !== "string" || !e["pointer"].startsWith("/")) {
        return reply.code(400).send({ error: "each edit needs a JSON-pointer 'pointer'" });
      }
      edits.push({ pointer: e["pointer"], value: e["value"] });
    }
    const expectedMtime = body["expectedMtime"];
    const result = sendBridgeFailure(
      reply,
      callConfigCli({
        op: "save",
        target,
        expected_mtime: typeof expectedMtime === "number" ? expectedMtime : null,
        edits,
      }),
    );
    if (result === null) return reply;
    app.log.info(`config saved: ${target} (${String(edits.length)} field(s))`);
    return {
      ok: true,
      mtime: result["mtime"] ?? null,
      backup: result["backup"] ?? null,
      issues: result["issues"] ?? [],
    };
  });

  // The console's own display preferences: its own store, no blast radius, so they save on change
  // rather than through a staged section.
  app.get("/api/config/prefs", async () => ({ prefs: getPrefs(config) }));

  app.post("/api/config/prefs", async (req, reply) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const key = body["key"];
    if (typeof key !== "string" || key === "") {
      return reply.code(400).send({ error: "key required" });
    }
    setPref(config, key, body["value"]);
    return { prefs: getPrefs(config) };
  });
}
