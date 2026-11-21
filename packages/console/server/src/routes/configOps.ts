import type { FastifyInstance, FastifyReply } from "fastify";
import type { ConsoleConfig } from "../config.js";
import { callConfigCli, statusForCode, type BridgeOk, type BridgeRequest, type BridgeResult } from "../services/configBridge.js";
import { readLockStatus } from "../services/liveLock.js";
import { backtestResultsOn, getFeatures, invalidateFeatures } from "../services/featuresBridge.js";
import { invalidateAutomationsBuilder } from "../services/automationsBuilderBridge.js";
import { invalidateAutomationGroups } from "../services/automationsGroupsBridge.js";
import { readGroupBudgetFor } from "../readers/groupBudget.js";
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

/** A window key as the automations module writes one (`enrolment.windows`): `<source>:<start>:<end>`. */
/** An underlying's root as the full set spells one (backtest `fullset.SYMBOL`). */
export const FULLSET_SYMBOL = /^[A-Z][A-Z.]{0,5}$/;
export const WINDOW_KEY = /^[a-z0-9-]{1,48}:\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}$/;
/** A strategy's name or a setting's key: the module's own words, checked by shape here. */
export const STRATEGY_NAME = /^[a-z][a-z0-9_]{0,39}$/;

/** An automation's name, as the backtest package and the module spell it. */
const AUTOMATION_NAME = /^[a-z0-9][a-z0-9-]{1,47}$/;

/** Settings of plain values, each keyed by a setting's name; null when anything else. */
function plainSettings(v: unknown): Record<string, string | number | boolean> | null {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return null;
  const ok = Object.entries(v as Record<string, unknown>).every(([k, x]) => STRATEGY_NAME.test(k) && ["string", "number", "boolean"].includes(typeof x));
  return ok ? (v as Record<string, string | number | boolean>) : null;
}
/** An idea search's result name, and one idea's id in it (`dim:kind:index`, at most two joined by &). */
export const IDEA_SEARCH = /^ideas-[a-z0-9][a-z0-9-]{1,47}-\d{10}$/;
export const IDEA_ID = /^[A-Za-z_:-]+:(bin|below|above):\d(&[A-Za-z_:-]+:(bin|below|above):\d)?$/;
/** A settings grid's result name, and one setting's id in it (`axis=value`, `width=x1.5`, `axis=off`). */
export const GRID = /^grid-[a-z0-9][a-z0-9-]{1,47}-\d{10}$/;
export const SETTING_ID = /^[a-z_]+=(off|x?\d+(\.\d+)?)$/;

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

  /**
   * "Today, live": the module decides one automation now as paper would (`cli builder-today`), on a
   * read-only snapshot of the stream cache and the paper ledger. A POST only because it may carry an
   * unsaved edit; it writes nothing and places nothing.
   */
  app.post("/api/automations/builder/today", async (req, reply) => {
    const parsed = automationBody((req.body ?? {}) as Record<string, unknown>);
    if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_today", ...parsed }));
    if (result === null) return reply;
    const { ok: _ok, ...today } = result;
    return today;
  });

  /**
   * The payoff under the Builder's legs: the structure the module's rules would take and its P&L at
   * expiry (`cli builder-payoff`), on live quotes or the last session our own recorder holds, with the
   * chain around the legs. A POST only because it may carry an unsaved edit; it writes nothing.
   */
  app.post("/api/automations/builder/payoff", async (req, reply) => {
    const parsed = automationBody((req.body ?? {}) as Record<string, unknown>);
    if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_payoff", ...parsed }));
    if (result === null) return reply;
    const { ok: _ok, ...payoff } = result;
    return payoff;
  });

  /**
   * The strategy selector: a strategy's legs and expiry from its settings, and a whole automation from
   * it for the wizard (`cli builder-strategy`). Sends the strategy's name and its settings (plain
   * values only) and nothing else; writes nothing.
   */
  app.post("/api/automations/builder/strategy", async (req, reply) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const name = body["strategy"];
    const settings = body["settings"] ?? {};
    if (typeof name !== "string" || !STRATEGY_NAME.test(name)) return reply.code(400).send({ error: "strategy: a strategy's name" });
    if (typeof settings !== "object" || settings === null || Array.isArray(settings)) return reply.code(400).send({ error: "settings: an object" });
    const plain = Object.entries(settings as Record<string, unknown>).every(([k, v]) => STRATEGY_NAME.test(k) && ["string", "number", "boolean"].includes(typeof v));
    if (!plain) return reply.code(400).send({ error: "settings: plain values only" });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_strategy", strategy: name, settings: settings as Record<string, string | number | boolean> }));
    if (result === null) return reply;
    const { ok: _ok, ...answer } = result;
    return answer;
  });

  /**
   * The entry presets: an entry preset's entry and the gates of the filters stacked on it
   * (`cli builder-entry`). Sends the preset's name, its settings and each filter's name and settings
   * (plain values only) and nothing else; writes nothing.
   */
  app.post("/api/automations/builder/entry", async (req, reply) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const name = body["entry"];
    const settings = plainSettings(body["settings"] ?? {});
    const filters = body["filters"] ?? [];
    if (typeof name !== "string" || !STRATEGY_NAME.test(name)) return reply.code(400).send({ error: "entry: an entry preset's name" });
    if (settings === null) return reply.code(400).send({ error: "settings: an object of plain values" });
    if (!Array.isArray(filters) || filters.length > 20) return reply.code(400).send({ error: "filters: a list" });
    const stacked: Array<{ filter: string; settings: Record<string, string | number | boolean> }> = [];
    for (const f of filters as unknown[]) {
      const one = (f ?? {}) as Record<string, unknown>;
      const s = plainSettings(one["settings"] ?? {});
      if (typeof one["filter"] !== "string" || !STRATEGY_NAME.test(one["filter"]) || s === null) return reply.code(400).send({ error: "filters: each a filter's name and plain settings" });
      stacked.push({ filter: one["filter"], settings: s });
    }
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_entry", entry: name, settings, filters: stacked }));
    if (result === null) return reply;
    const { ok: _ok, ...answer } = result;
    return answer;
  });

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
   * The Automations › Groups page, through the orchestrator's editor like every config write here
   * (configcli `automation_groups*`, `orchestrator.groupedit`). The view and the check write nothing;
   * the save runs the module's check and writes only what it returned: the `groups` block, each moved
   * automation's `group`, and each switched automation's `enabled` and the start the module gave it
   * (the next session, switched on). The automations module is paper only, so a switch here starts or
   * stops paper entries, never an order. The page sends switches as true or false; the start is never
   * the page's.
   */
  app.get("/api/automations/groups/edit", async (_req, reply) => {
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_groups" }));
    if (result === null) return reply;
    const { ok: _ok, ...view } = result;
    return view;
  });

  /** A group's budget from its members' pinned backtest runs (`readers/groupBudget.ts`): a suggestion
   *  beside the max risk and shares, read only. */
  app.get("/api/automations/groups/budget", async (req, reply) => {
    const group = String(((req.query ?? {}) as Record<string, unknown>)["group"] ?? "");
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,46}$/.test(group)) return reply.code(400).send({ error: "group: a group's name" });
    return readGroupBudgetFor(group);
  });

  type GroupsBody = { groups: Record<string, unknown>; members: Record<string, string | null>; switches: Record<string, boolean> };
  const groupsBody = (body: Record<string, unknown>): GroupsBody | string => {
    const groups = body["groups"];
    const members = body["members"] ?? {};
    const switches = body["switches"] ?? {};
    if (typeof groups !== "object" || groups === null || Array.isArray(groups)) return "groups must be an object";
    if (typeof members !== "object" || members === null || Array.isArray(members)) return "members must be an object";
    if (typeof switches !== "object" || switches === null || Array.isArray(switches)) return "switches must be an object";
    for (const [name, g] of Object.entries(members as Record<string, unknown>)) {
      if (name.length === 0 || name.length > 48) return "a member's name is up to 48 characters";
      if (g !== null && (typeof g !== "string" || g.length === 0 || g.length > 48)) return `members.${name}: a group name (up to 48 characters) or null`;
    }
    for (const [name, on] of Object.entries(switches as Record<string, unknown>)) {
      if (name.length === 0 || name.length > 48) return "an automation's name is up to 48 characters";
      if (typeof on !== "boolean") return `switches.${name}: true or false`;
    }
    if (JSON.stringify(body).length > MAX_AUTOMATION_CHARS) return `the change is larger than ${MAX_AUTOMATION_CHARS} characters`;
    return { groups: groups as Record<string, unknown>, members: members as Record<string, string | null>, switches: switches as Record<string, boolean> };
  };

  app.post("/api/automations/groups/check", async (req, reply) => {
    const parsed = groupsBody((req.body ?? {}) as Record<string, unknown>);
    if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_groups_check", ...parsed }));
    if (result === null) return reply;
    return {
      ok: true,
      problems: result["problems"] ?? [],
      moves: result["moves"] ?? {},
      switches: result["switches"] ?? {},
      notes: result["notes"] ?? [],
      after: result["after"] ?? null,
    };
  });

  app.post("/api/automations/groups/save", async (req, reply) => {
    const parsed = groupsBody((req.body ?? {}) as Record<string, unknown>);
    if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
    const result = await callConfigCli({ op: "automation_groups_save", ...parsed });
    if (!result.ok) {
      const problems = (result as unknown as Record<string, unknown>)["problems"];
      return reply.code(statusForCode(result.code)).send({
        error: result.error,
        code: result.code,
        ...(Array.isArray(problems) ? { problems } : {}),
      });
    }
    invalidateAutomationGroups();
    invalidateAutomationsBuilder();
    const moved = Array.isArray(result["moved"]) ? (result["moved"] as string[]) : [];
    const switched = Array.isArray(result["switched"]) ? (result["switched"] as string[]) : [];
    app.log.info(`automation groups saved from the console: ${moved.length} moved, ${switched.length} switched`);
    return { ok: true, moved, switched, groups: result["groups"] ?? [], backup: result["backup"] ?? null };
  });

  /**
   * The Builder's backtest (#1180): one explore run of one saved automation, queued on the research
   * queue by the orchestrator (`automationrun.queue`, a `script` row of the automation backtest driver).
   * Descriptive, no trial and no verdict; it writes no config and turns nothing on. The orchestrator
   * refuses unless the automation's own suggestion says our data covers it. The name is sent, and at
   * most one of the suggestion's own window keys: the dates and the store are the module's, never the
   * page's, and the orchestrator refuses a key the module does not offer.
   */
  app.post("/api/automations/builder/backtest", async (req, reply) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const parsed = automationBody(body);
    if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
    const key = body["window"];
    if (key !== undefined && (typeof key !== "string" || !WINDOW_KEY.test(key))) {
      return reply.code(400).send({ error: "window is one of the suggestion's window keys" });
    }
    const ask: BridgeRequest =
      typeof key === "string" ? { op: "automation_backtest", name: parsed.name, window: key } : { op: "automation_backtest", name: parsed.name };
    const result = sendBridgeFailure(reply, await callConfigCli(ask));
    if (result === null) return reply;
    app.log.info(`automation backtest queued from the builder: ${parsed.name} (task ${String(result["task"] ?? "?")})`);
    return {
      ok: true,
      name: parsed.name,
      run: result["run"] ?? null,
      task: result["task"] ?? null,
      window: result["window"] ?? null,
      source: result["source"] ?? null,
      key: result["key"] ?? null,
    };
  });

  /**
   * Adding an underlying to the full set (#1253): what it would fetch (`fullset_plan`, the backtest
   * planner's dry run, counts only), and the addition itself (`fullset_add`): its fetches queued by the
   * orchestrator, capped per click and only while new work may use the vendor, with this automation's
   * explore backtest queued after them. The page sends the symbol and the automation's name; the span,
   * the cap and the rows are the orchestrator's.
   */
  app.get("/api/automations/builder/fullset-plan", async (req, reply) => {
    const symbol = String(((req.query ?? {}) as Record<string, unknown>)["symbol"] ?? "").toUpperCase();
    if (!FULLSET_SYMBOL.test(symbol)) return reply.code(400).send({ error: "symbol is an underlying's root" });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "fullset_plan", symbol }));
    if (result === null) return reply;
    return {
      symbol,
      from: result["from"] ?? null,
      through: result["through"] ?? null,
      requests: result["requests"] ?? {},
      concurrent: result["concurrent"] ?? null,
      cap_sessions: result["cap_sessions"] ?? null,
      within_cap: result["within_cap"] === true,
    };
  });

  app.post("/api/automations/builder/fullset-add", async (req, reply) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const parsed = automationBody(body);
    if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
    const symbol = String(body["symbol"] ?? "").toUpperCase();
    if (!FULLSET_SYMBOL.test(symbol)) return reply.code(400).send({ error: "symbol is an underlying's root" });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "fullset_add", symbol, run: parsed.name }));
    if (result === null) return reply;
    const run = (result["run"] ?? null) as Record<string, unknown> | null;
    app.log.info(`full set: ${symbol} added from the builder for ${parsed.name} (${String((result["tasks"] as unknown[] | undefined)?.length ?? 0)} rows)`);
    return {
      ok: true,
      symbol,
      from: result["from"] ?? null,
      through: result["through"] ?? null,
      tasks: Array.isArray(result["tasks"]) ? result["tasks"] : [],
      run: run && run["ok"] === true ? { task: run["task"] ?? null, run: run["run"] ?? null } : null,
      run_error: run && run["ok"] !== true ? String(run["error"] ?? "the backtest was not queued") : null,
    };
  });

  /**
   * The run card: that automation's queue rows and results. A licensed result (one that read the
   * vendor's store) is shown only while licensed results may be (`backtestResultsOn`); otherwise it is
   * left out and counted in `withheld`. Fail closed: a result not plainly unlicensed is licensed.
   */
  /**
   * The Builder's pin of one of the automation's own Builder runs as its backtest reference (`run` null
   * unpins), through the orchestrator's editor like every config write here (configcli
   * `automation_reference`, `orchestrator.referenceedit`): the module's check, the backtest package's word
   * that the run exists, then the `references` block alone. A pin changes nothing an automation trades.
   */
  app.post("/api/automations/builder/reference", async (req, reply) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const name = body["name"];
    const run = body["run"] ?? null;
    if (typeof name !== "string" || !AUTOMATION_NAME.test(name)) return reply.code(400).send({ error: "name: an automation's name" });
    if (run !== null && (typeof run !== "string" || !new RegExp(`^builder-${name}-\\d{10}$`).test(run))) {
      return reply.code(400).send({ error: "run: one of this automation's Builder runs, or null to unpin" });
    }
    const result = await callConfigCli({ op: "automation_reference", name, run });
    if (!result.ok) {
      const problems = (result as unknown as Record<string, unknown>)["problems"];
      return reply.code(statusForCode(result.code)).send({
        error: result.error,
        code: result.code,
        ...(Array.isArray(problems) ? { problems } : {}),
      });
    }
    invalidateAutomationsBuilder();
    app.log.info(`automation reference ${run === null ? "unpinned" : "pinned"} from the builder: ${name}`);
    return { ok: true, name, run };
  });

  /**
   * An automation's page retires or reinstates it, through the orchestrator's editor like every config write
   * here (configcli `automation_retire`, `orchestrator.retireedit`): the module's check, then that
   * automation's `retired` (the date, or false) and `enabled` (false either way: reinstating never
   * switches one on), nothing else. Paper only; what a retired automation holds is still managed.
   */
  app.post("/api/automations/retire", async (req, reply) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const name = body["name"];
    const retired = body["retired"];
    if (typeof name !== "string" || !AUTOMATION_NAME.test(name)) return reply.code(400).send({ error: "name: an automation's name" });
    if (typeof retired !== "boolean") return reply.code(400).send({ error: "retired: true or false" });
    const result = await callConfigCli({ op: "automation_retire", name, retired });
    if (!result.ok) {
      const problems = (result as unknown as Record<string, unknown>)["problems"];
      return reply.code(statusForCode(result.code)).send({
        error: result.error,
        code: result.code,
        ...(Array.isArray(problems) ? { problems } : {}),
      });
    }
    invalidateAutomationGroups();
    invalidateAutomationsBuilder();
    app.log.info(`automation ${retired ? "retired" : "reinstated"} from the console: ${name}`);
    return { ok: true, name, retired: result["retired"] ?? retired, notes: result["notes"] ?? [], backup: result["backup"] ?? null };
  });

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

  /**
   * One run's whole card (tiles, curve, years, skips, what it changed, the trade log), read by the
   * backtest package. A licensed result is refused while licensed results may not be shown; fail
   * closed, as the list is.
   */
  app.get("/api/automations/builder/run", async (req, reply) => {
    const q = (req.query ?? {}) as Record<string, unknown>;
    const name = String(q["name"] ?? "");
    const run = String(q["run"] ?? "");
    if (name === "" || name.length > 48 || run === "" || run.length > 80) return reply.code(400).send({ error: "name and run required" });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_run", name, run }));
    if (result === null) return reply;
    if (result["licensed"] !== false && !backtestResultsOn(await getFeatures())) {
      return reply.code(403).send({ error: "a licensed result, not shown while licensed results are withheld", code: "withheld" });
    }
    const { ok: _ok, ...card } = result;
    return card;
  });

  /**
   * The idea search (#1294): "Find ideas" queues one search of the automation's newest long enough
   * Builder run (`ideaqueue.queue`, a `script` row; the run and the split are the backtest package's),
   * and the page reads the searches back. Declare drafts one surviving idea's register row into its
   * result; the held-out test reads a declared idea's later sessions once. Neither writes a config or
   * the register. A licensed result is shown, declared from or tested only while licensed results may
   * be shown; fail closed, as the run card is.
   */
  app.post("/api/automations/builder/ideas", async (req, reply) => {
    const parsed = automationBody((req.body ?? {}) as Record<string, unknown>);
    if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_ideas", name: parsed.name }));
    if (result === null) return reply;
    app.log.info(`idea search queued from the builder: ${parsed.name} (task ${String(result["task"] ?? "?")})`);
    return {
      ok: true,
      name: parsed.name,
      search: result["search"] ?? null,
      task: result["task"] ?? null,
      run: result["run"] ?? null,
      split: result["split"] ?? null,
    };
  });

  app.get("/api/automations/builder/ideas", async (req, reply) => {
    const name = String(((req.query ?? {}) as Record<string, unknown>)["name"] ?? "");
    if (name === "" || name.length > 48) return reply.code(400).send({ error: "name (up to 48 characters) required" });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_idea_searches", name }));
    if (result === null) return reply;
    const open = backtestResultsOn(await getFeatures());
    const all = Array.isArray(result["searches"]) ? (result["searches"] as Array<Record<string, unknown>>) : [];
    const searches = all.filter((s) => open || s["licensed"] === false);
    return {
      name,
      tasks: Array.isArray(result["tasks"]) ? result["tasks"] : [],
      searches,
      withheld: all.length - searches.length,
      results_unavailable: result["results_unavailable"] === true,
    };
  });

  /** One search read whole, or a reply already sent: refused when it is licensed and those are withheld. */
  const ideaSearch = async (reply: FastifyReply, name: string, id: string): Promise<BridgeOk | null> => {
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_idea_search", name, id }));
    if (result === null) return null;
    if (result["licensed"] !== false && !backtestResultsOn(await getFeatures())) {
      reply.code(403).send({ error: "a licensed result, not shown while licensed results are withheld", code: "withheld" });
      return null;
    }
    return result;
  };

  app.get("/api/automations/builder/idea-search", async (req, reply) => {
    const q = (req.query ?? {}) as Record<string, unknown>;
    const name = String(q["name"] ?? "");
    const id = String(q["id"] ?? "");
    if (!IDEA_SEARCH.test(id) || name === "" || name.length > 48) return reply.code(400).send({ error: "name and id required" });
    const result = await ideaSearch(reply, name, id);
    if (result === null) return reply;
    const { ok: _ok, ...search } = result;
    return search;
  });

  for (const [path, op] of [
    ["/api/automations/builder/idea-declare", "automation_idea_declare"],
    ["/api/automations/builder/idea-holdout", "automation_idea_holdout"],
  ] as const) {
    app.post(path, async (req, reply) => {
      const body = (req.body ?? {}) as Record<string, unknown>;
      const parsed = automationBody(body);
      if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
      const id = body["id"];
      const idea = body["idea"];
      if (typeof id !== "string" || !IDEA_SEARCH.test(id) || typeof idea !== "string" || !IDEA_ID.test(idea)) {
        return reply.code(400).send({ error: "id is one of its idea searches and idea one idea in it" });
      }
      // Read first: a licensed search withheld from view is neither declared from nor spent.
      if ((await ideaSearch(reply, parsed.name, id)) === null) return reply;
      const result = sendBridgeFailure(reply, await callConfigCli({ op, name: parsed.name, id, idea }));
      if (result === null) return reply;
      app.log.info(`${op === "automation_idea_declare" ? "idea declared" : "held-out test read"} from the builder: ${parsed.name} ${id} ${idea}`);
      const { ok: _ok, ...out } = result;
      return { ok: true, ...out };
    });
  }

  /**
   * The settings grid (#1294): "Try settings" queues one replay of the automation and every exit and
   * width setting of the grid beside it (`gridqueue.queue`, a `script` row; the run and the split are
   * the backtest package's), and the page reads the grids back, never their trades. Declare and the
   * held-out test are the idea search's, by setting. A licensed result is shown, declared from or tested
   * only while licensed results may be shown; fail closed.
   */
  app.post("/api/automations/builder/grid", async (req, reply) => {
    const parsed = automationBody((req.body ?? {}) as Record<string, unknown>);
    if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_grid", name: parsed.name }));
    if (result === null) return reply;
    app.log.info(`settings grid queued from the builder: ${parsed.name} (task ${String(result["task"] ?? "?")})`);
    return {
      ok: true,
      name: parsed.name,
      grid: result["grid"] ?? null,
      task: result["task"] ?? null,
      run: result["run"] ?? null,
      split: result["split"] ?? null,
    };
  });

  app.get("/api/automations/builder/grids", async (req, reply) => {
    const name = String(((req.query ?? {}) as Record<string, unknown>)["name"] ?? "");
    if (name === "" || name.length > 48) return reply.code(400).send({ error: "name (up to 48 characters) required" });
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_grids", name }));
    if (result === null) return reply;
    const open = backtestResultsOn(await getFeatures());
    const all = Array.isArray(result["grids"]) ? (result["grids"] as Array<Record<string, unknown>>) : [];
    const grids = all.filter((g) => open || g["licensed"] === false);
    return {
      name,
      tasks: Array.isArray(result["tasks"]) ? result["tasks"] : [],
      grids,
      withheld: all.length - grids.length,
      results_unavailable: result["results_unavailable"] === true,
    };
  });

  /** One grid read whole, or a reply already sent: refused when it is licensed and those are withheld. */
  const gridResult = async (reply: FastifyReply, name: string, id: string): Promise<BridgeOk | null> => {
    const result = sendBridgeFailure(reply, await callConfigCli({ op: "automation_grid_result", name, id }));
    if (result === null) return null;
    if (result["licensed"] !== false && !backtestResultsOn(await getFeatures())) {
      reply.code(403).send({ error: "a licensed result, not shown while licensed results are withheld", code: "withheld" });
      return null;
    }
    return result;
  };

  app.get("/api/automations/builder/grid-result", async (req, reply) => {
    const q = (req.query ?? {}) as Record<string, unknown>;
    const name = String(q["name"] ?? "");
    const id = String(q["id"] ?? "");
    if (!GRID.test(id) || name === "" || name.length > 48) return reply.code(400).send({ error: "name and id required" });
    const result = await gridResult(reply, name, id);
    if (result === null) return reply;
    const { ok: _ok, ...grid } = result;
    return grid;
  });

  for (const [path, op] of [
    ["/api/automations/builder/grid-declare", "automation_grid_declare"],
    ["/api/automations/builder/grid-holdout", "automation_grid_holdout"],
  ] as const) {
    app.post(path, async (req, reply) => {
      const body = (req.body ?? {}) as Record<string, unknown>;
      const parsed = automationBody(body);
      if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
      const id = body["id"];
      const setting = body["setting"];
      if (typeof id !== "string" || !GRID.test(id) || typeof setting !== "string" || !SETTING_ID.test(setting)) {
        return reply.code(400).send({ error: "id is one of its settings grids and setting one setting in it" });
      }
      // Read first: a licensed grid withheld from view is neither declared from nor spent.
      if ((await gridResult(reply, parsed.name, id)) === null) return reply;
      const result = sendBridgeFailure(reply, await callConfigCli({ op, name: parsed.name, id, setting }));
      if (result === null) return reply;
      app.log.info(`${op === "automation_grid_declare" ? "setting declared" : "held-out test read"} from the builder: ${parsed.name} ${id} ${setting}`);
      const { ok: _ok, ...out } = result;
      return { ok: true, ...out };
    });
  }

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
