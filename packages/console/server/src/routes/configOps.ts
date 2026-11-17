import type { FastifyInstance, FastifyReply } from "fastify";
import type { ConsoleConfig } from "../config.js";
import { callConfigCli, statusForCode, type BridgeOk, type BridgeResult } from "../services/configBridge.js";
import { readLockStatus } from "../services/liveLock.js";
import { backtestResultsOn, getFeatures, invalidateFeatures } from "../services/featuresBridge.js";
import { invalidateAutomationsBuilder } from "../services/automationsBuilderBridge.js";
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

const MAX_PREF_KEY_CHARS = 64;
const MAX_PREF_VALUE_CHARS = 16_384;
const MAX_AUTOMATION_CHARS = 32_768;

const TARGETS = ["orchestrator", "meic", "flies", "gex", "earnings", "streamer", "meic-risk", "manage"] as const;

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
    // Side by side: each target is its own subprocess, and none reads another's answer.
    const loaded = await Promise.all(TARGETS.map((id) => callConfigCli({ op: "load", target: id })));
    for (const [i, id] of TARGETS.entries()) {
      const result = loaded[i]!;
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

  /**
   * What the suite has turned on, as the orchestrator resolves it — the one source the rail, the
   * header, the Overview and the turned-off card read. Always 200: a failed bridge is `{ok: false,
   * error}` in the body, and the browser shows everything beside a warning chip (fail open).
   */
  app.get("/api/features", async () => getFeatures());

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
    if (sendBridgeFailure(reply, await callConfigCli({ op: "set_halt", present })) === null) return reply;
    invalidateFeatures();
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
      await callConfigCli({
        op: "save",
        target,
        expected_mtime: typeof expectedMtime === "number" ? expectedMtime : null,
        edits,
      }),
    );
    if (result === null) return reply;
    // A save can switch a module or a feature off (or back on); the next features read asks again.
    invalidateFeatures();
    app.log.info(`config saved: ${target} (${String(edits.length)} field(s))`);
    return {
      ok: true,
      mtime: result["mtime"] ?? null,
      backup: result["backup"] ?? null,
      issues: result["issues"] ?? [],
    };
  });

  /**
   * The Builder's editor (#1180), one automation at a time, through the orchestrator's editor like
   * every config write here. `draft` is the module's own check and writes nothing: without an
   * `automation` it returns the automation as the editor starts from it. `save` runs that check and
   * writes the one entry it returned, always off; turning an automation on is never a save.
   */
  const automationBody = (body: Record<string, unknown>): { name: string; automation?: Record<string, unknown> } | string => {
    const name = body["name"];
    if (typeof name !== "string" || name === "" || name.length > 48) return "name (up to 48 characters) required";
    const automation = body["automation"];
    if (automation === undefined) return { name };
    if (typeof automation !== "object" || automation === null || Array.isArray(automation)) return "automation must be an object";
    if (JSON.stringify(automation).length > MAX_AUTOMATION_CHARS) return `automation larger than ${MAX_AUTOMATION_CHARS} characters`;
    return { name, automation: automation as Record<string, unknown> };
  };

  app.post("/api/automations/builder/draft", async (req, reply) => {
    const parsed = automationBody((req.body ?? {}) as Record<string, unknown>);
    if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_draft", ...parsed }));
    if (result === null) return reply;
    return {
      ok: true,
      name: result["name"] ?? parsed.name,
      ...("automation" in result ? { automation: result["automation"] ?? null } : {}),
      ...("problems" in result ? { problems: result["problems"] ?? [] } : {}),
      ...("view" in result ? { view: result["view"] ?? null } : {}),
      ...("new" in result ? { new: result["new"] === true } : {}),
    };
  });

  app.post("/api/automations/builder/save", async (req, reply) => {
    const parsed = automationBody((req.body ?? {}) as Record<string, unknown>);
    if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
    if (parsed.automation === undefined) return reply.code(400).send({ error: "automation required" });
    const result = await callConfigCli({ op: "automation_save", name: parsed.name, automation: parsed.automation });
    if (!result.ok) {
      const problems = (result as unknown as Record<string, unknown>)["problems"];
      return reply.code(statusForCode(result.code)).send({
        error: result.error,
        code: result.code,
        ...(Array.isArray(problems) ? { problems } : {}),
      });
    }
    invalidateAutomationsBuilder();
    app.log.info(`automation saved from the builder: ${parsed.name} (${result["added"] === true ? "added" : "replaced"}, off)`);
    return { ok: true, name: parsed.name, added: result["added"] === true, enabled: false, backup: result["backup"] ?? null };
  });

  /**
   * The Builder's backtest (#1180): one explore run of one saved automation, queued on the research
   * queue by the orchestrator (`automationrun.queue`, a `script` row of the automation backtest driver).
   * Descriptive, no trial and no verdict; it writes no config and turns nothing on. The orchestrator
   * refuses unless the automation's own suggestion says our data covers it. Only the name is sent:
   * the window and the source are the module's, never the page's.
   */
  app.post("/api/automations/builder/backtest", async (req, reply) => {
    const parsed = automationBody((req.body ?? {}) as Record<string, unknown>);
    if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_backtest", name: parsed.name }));
    if (result === null) return reply;
    app.log.info(`automation backtest queued from the builder: ${parsed.name} (task ${String(result["task"] ?? "?")})`);
    return {
      ok: true,
      name: parsed.name,
      run: result["run"] ?? null,
      task: result["task"] ?? null,
      window: result["window"] ?? null,
      source: result["source"] ?? null,
    };
  });

  /**
   * The run card: that automation's queue rows and results. A licensed result (one that read the
   * vendor's store) is shown only while licensed results may be (`backtestResultsOn`); otherwise it is
   * left out and counted in `withheld`. Fail closed: a result not plainly unlicensed is licensed.
   */
  app.get("/api/automations/builder/runs", async (req, reply) => {
    const name = String(((req.query ?? {}) as Record<string, unknown>)["name"] ?? "");
    if (name === "" || name.length > 48) return reply.code(400).send({ error: "name (up to 48 characters) required" });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_runs", name }));
    if (result === null) return reply;
    const open = backtestResultsOn(await getFeatures());
    const all = Array.isArray(result["runs"]) ? (result["runs"] as Array<Record<string, unknown>>) : [];
    const runs = all.filter((r) => open || r["licensed"] === false);
    return {
      name,
      tasks: Array.isArray(result["tasks"]) ? result["tasks"] : [],
      runs,
      withheld: all.length - runs.length,
      results_unavailable: result["results_unavailable"] === true,
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
    // Keys are open-ended (per-table layouts), so bound them rather than allow-list: a buggy client
    // must not be able to grow the prefs table without limit.
    if (key.length > MAX_PREF_KEY_CHARS) {
      return reply.code(400).send({ error: `key longer than ${MAX_PREF_KEY_CHARS} characters` });
    }
    if ((JSON.stringify(body["value"]) ?? "").length > MAX_PREF_VALUE_CHARS) {
      return reply.code(413).send({ error: `value larger than ${MAX_PREF_VALUE_CHARS} characters` });
    }
    setPref(config, key, body["value"]);
    return { prefs: getPrefs(config) };
  });
}
