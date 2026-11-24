import fs from "node:fs";
import type { FastifyInstance, FastifyReply } from "fastify";
import { consoleConfigPath } from "@console/shared/paths";
import type { ArmableLoop, LiveArmReadout, LiveArmRefusal, LiveArmingState, LiveLoopSwitch } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { meicLiveAccounts } from "../readers/meicLive.js";
import { beginAttempt, listAttempts, recoverAttempts, runAttempt } from "../services/liveArmAttempts.js";
import { buildReadout, isArmable, issueDialog, runDisarm, spendDialog } from "../services/liveArmingBridge.js";
import { readMeicReadiness } from "../services/meicStatusBridge.js";

/**
 * The Live page's arm switches, server half. The one write the Live page has.
 *
 * Gating is the mutating-surface posture of `security.ts` (loopback Host, CSRF, JSON), the same bar the
 * Config page's writes meet, plus the three things this route adds because it can put a live loop on:
 *
 *  - Off unless `live_arming` is `true` in console.json. Checked here on every route, not only by
 *    hiding the switch: with it off, every route below answers 403 and nothing else runs.
 *  - Only `meic` and `ladder`. Anything else (flies and bwb are retired, a second login's account) is
 *    refused before a process is started.
 *  - An arm needs the one-use dialog token issued with a readout, the literal word `YES` in the body
 *    (strict equality, no trimming, no case folding), a loop that is ready and NOT halted when the
 *    confirm arrives, and a readout that has not changed since the person read it.
 *
 * This file holds no arming logic. The module's own command writes the record (`liveArmingBridge.ts`);
 * the halt flag is never touched here, and while it is up the arm is refused.
 */

/** Whether the switches are shown and the routes answer: a literal `true`, nothing else, unreadable is off. */
export function liveArmingEnabled(config: ConsoleConfig): boolean {
  try {
    const raw = JSON.parse(fs.readFileSync(consoleConfigPath(config.paths.cherrypick), "utf-8")) as Record<string, unknown>;
    return raw["live_arming"] === true;
  } catch {
    return false;
  }
}

function refuse(reply: FastifyReply, status: number, body: LiveArmRefusal): FastifyReply {
  return reply.code(status).send(body);
}

/** The switches the page may draw: a working one for each armable loop, a locked one for a second login. */
async function switchesFor(config: ConsoleConfig): Promise<LiveLoopSwitch[]> {
  const out: LiveLoopSwitch[] = [
    { loop: "meic", state: "armable", lockedReason: null },
    { loop: "ladder", state: "armable", lockedReason: null },
  ];
  for (const login of meicLiveAccounts(config)) {
    const r = await readMeicReadiness(Date.now(), login);
    const why = r.ok && !r.ready ? r.blockers[0] : null;
    out.push({
      loop: `meic.${login}`,
      state: "locked",
      lockedReason: why ?? `A second login is armed from the command line: /live-meic-start --login ${login}`,
    });
  }
  return out;
}

export function registerLiveArmingRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  // A console that restarts mid-arm resolves the attempt by asking the module. Never runs an arm again.
  void recoverAttempts(config).catch(() => undefined);

  const off = (reply: FastifyReply): FastifyReply => refuse(reply, 403, { error: "the arm switches are off (live_arming in console.json)", code: "arming_off" });

  /** Whether the switches show, which are locked, and the attempts the server holds. File reads only. */
  app.get("/api/live/arming", async (): Promise<LiveArmingState> => {
    if (!liveArmingEnabled(config)) return { enabled: false, switches: [], attempts: [] };
    return { enabled: true, switches: await switchesFor(config), attempts: listAttempts(config) };
  });

  /** The dialog's content: the module's own readiness, read now, with the disclaimer and a one-use token. */
  app.get<{ Params: { loop: string } }>("/api/live/arming/:loop/readout", async (req, reply) => {
    if (!liveArmingEnabled(config)) return off(reply);
    const { loop } = req.params;
    if (!isArmable(loop)) return refuse(reply, 404, { error: `${loop} has no arm switch here`, code: "not_armable" });
    const r = await buildReadout(loop);
    const readout: LiveArmReadout = { ...r.readout, dialogId: issueDialog(loop, r) };
    return readout;
  });

  app.post<{ Params: { loop: string } }>("/api/live/arming/:loop/arm", async (req, reply) => {
    if (!liveArmingEnabled(config)) return off(reply);
    const loop = req.params.loop;
    if (!isArmable(loop)) return refuse(reply, 403, { error: `${loop} cannot be armed from the console`, code: "not_armable" });
    const body = (req.body ?? {}) as Record<string, unknown>;
    // The word the person typed, exactly: a boolean, a lower-case "yes" or a padded "YES " is not it.
    if (body["confirm"] !== "YES") return refuse(reply, 400, { error: "type YES, in capitals, to confirm", code: "confirm_required" });
    // The token is spent before anything else is looked at: a confirmation is never reused.
    const dialog = spendDialog(body["dialogId"], loop);
    if (dialog === null) {
      return refuse(reply, 409, { error: "this confirmation is not for a readout the server served, or it was used or expired: open the dialog again", code: "readout_expired" });
    }
    const now = await buildReadout(loop);
    const o = now.readout;
    if (!o.ok) return refuse(reply, 502, { error: o.error ?? "the loop's status could not be read", code: "status_unreadable" });
    if (o.halted) {
      return refuse(reply, 409, { error: "the halt flag is up. Clear the halt first; arming never undoes a halt.", code: "halted", readout: o });
    }
    if (!o.ready) {
      return refuse(reply, 409, { error: "the loop is not ready to arm", code: "not_ready", blockers: o.blockers, readout: o });
    }
    if (now.hash !== dialog.hash) {
      const fresh: LiveArmReadout = { ...o, dialogId: issueDialog(loop, now) };
      return refuse(reply, 409, { error: "the loop's readout changed after you read it: read it again", code: "readout_changed", readout: fresh });
    }
    if (o.disclaimer === null) {
      return refuse(reply, 500, { error: "DISCLAIMER.md was not found, so it cannot be shown. Nothing was armed.", code: "no_disclaimer" });
    }
    const { attempt, started } = beginAttempt(config, loop as ArmableLoop, o.session ?? "", o.armedToday);
    if (started) {
      app.log.info(`live arm: ${loop} confirmed from the console (attempt ${attempt.id})`);
      // Not tied to this request: it finishes whether or not the page is still there.
      void runAttempt(config, attempt);
    }
    return reply.code(202).send({ attempt });
  });

  /** Disarm: the module's own `--disarm`, after the page's plain confirm. Allowed while halted. */
  app.post<{ Params: { loop: string } }>("/api/live/arming/:loop/disarm", async (req, reply) => {
    if (!liveArmingEnabled(config)) return off(reply);
    const loop = req.params.loop;
    if (!isArmable(loop)) return refuse(reply, 403, { error: `${loop} cannot be disarmed from the console`, code: "not_armable" });
    const body = (req.body ?? {}) as Record<string, unknown>;
    if (body["disarm"] !== true) return refuse(reply, 400, { error: "disarm: true required", code: "confirm_required" });
    const out = await runDisarm(loop);
    app.log.info(`live disarm: ${loop} from the console (${out.ok ? "removed" : "nothing removed"})`);
    if (out.failed) return refuse(reply, 502, { error: out.error ?? "the disarm command did not answer", code: "unavailable" });
    // "Nothing was armed" is the module's honest ok:false: the loop is unarmed, which is what was asked.
    return { ok: true, removed: out.ok, detail: typeof out.doc?.["detail"] === "string" ? out.doc["detail"] : out.ok ? "disarmed" : "nothing was armed" };
  });
}
