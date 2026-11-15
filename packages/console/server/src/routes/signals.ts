import type { FastifyInstance, FastifyReply } from "fastify";
import { isSignalStudyName, type BridgedResult, type SignalStudyList, type SignalStudyResult } from "@console/shared";
import { backtestOn, getFeatures } from "../services/featuresBridge.js";
import { readSignalStudies, readSignalStudy } from "../services/signalStudyBridge.js";

/**
 * `GET /api/signals` and `GET /api/signals/:name` — Compare → signals, the signal studies of
 * `packages/backtest` (#1179).
 *
 * **Open to every reader, except a licensed result.** A study over technicals' daily history or the
 * gex recorder's minutes is ours, so these routes answer without the backtest feature, and keep
 * answering after a vendor subscription ends. A study over the vendor's minute store is derived from
 * licensed data, so it is shown only while the orchestrator's features answer says `backtest: true`
 * (`featuresBridge.backtestOn`, the Backtest page's own gate): otherwise the list leaves it out and
 * counts it in `withheld`, and the one-result route answers it with the unknown-route 404, so a closed
 * gate reads as no such result.
 *
 * A name is checked against the module's own spelling before anything spawns; anything else is a 400.
 */

function refuse(reply: FastifyReply): FastifyReply {
  return reply.code(404).send({ error: "not found" });
}

export function registerSignalStudyRoutes(app: FastifyInstance): void {
  app.get("/api/signals", async (): Promise<BridgedResult<SignalStudyList>> => {
    const read = await readSignalStudies();
    if (!read.ok || read.data === null) return { ok: false, data: null, error: read.error };
    const open = backtestOn(await getFeatures());
    const shown = read.data.studies.filter((s) => open || s.licensed === false);
    return {
      ok: true,
      data: { studies: shown, unreadable: read.data.unreadable, withheld: read.data.studies.length - shown.length },
      error: null,
    };
  });

  app.get<{ Params: { name: string } }>(
    "/api/signals/:name",
    async (req, reply): Promise<BridgedResult<SignalStudyResult> | FastifyReply> => {
      if (!isSignalStudyName(req.params.name)) return reply.code(400).send({ error: "not a signal study name" });
      const read = await readSignalStudy(req.params.name);
      // Fail closed: a licensed result, or one whose licence cannot be read, needs the gate open.
      if (read.ok && read.data !== null && read.data.licensed !== false && !backtestOn(await getFeatures())) return refuse(reply);
      return read;
    },
  );
}
