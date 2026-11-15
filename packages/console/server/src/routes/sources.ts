import type { FastifyInstance, FastifyReply } from "fastify";
import {
  SOURCE_CADENCES,
  SOURCE_EXPIRIES,
  SOURCE_FIELDS,
  SOURCE_SYMBOL,
  type BridgedResult,
  type SourceCoverage,
  type SourcesSummary,
} from "@console/shared";
import { backtestOn, backtestResultsOn, getFeatures } from "../services/featuresBridge.js";
import { readCoverage, readSources, type CoverageRequest } from "../services/sourcesBridge.js";

/**
 * `GET /api/sources` and `GET /api/sources/coverage` — Backtest → sources, the source registry
 * (`cherrypick.core.sources`, #1194).
 *
 * **Open to every reader, except the vendor's.** Our own stores and public data are listed whatever
 * the licence. A vendor source (`licensed`) is listed only while its results may be shown
 * (`backtestResultsOn`, the same gate as the runs and data tabs); otherwise it is left out and counted
 * in `withheld`. Coverage leaves licensed sources out unless asked (`licensed=1`), and asking is
 * refused unless new work may use the vendor (`backtestOn`).
 *
 * Every value is checked against core's own lists before anything spawns; anything else is a 400.
 */

const DAY = /^\d{4}-\d{2}-\d{2}$/;

type CoverageQuery = Partial<Record<"field" | "symbol" | "from" | "to" | "cadence" | "expiry" | "licensed", string>>;

/** The request a query asks for, or why it is refused. Pure: exported for the tests. */
export function parseCoverage(q: CoverageQuery): CoverageRequest | string {
  const field = q.field ?? "";
  if (!(SOURCE_FIELDS as readonly string[]).includes(field)) return `field must be one of ${SOURCE_FIELDS.join(", ")}`;
  const symbol = (q.symbol ?? "").trim().toUpperCase();
  if (!SOURCE_SYMBOL.test(symbol)) return "symbol is letters, digits and / . - (up to 16)";
  const start = q.from ?? "";
  const end = q.to ?? "";
  if (!DAY.test(start) || !DAY.test(end)) return "from and to are dates, YYYY-MM-DD";
  if (end < start) return "to is before from";
  const req: CoverageRequest = { field, symbol, start, end, licensed: q.licensed === "1" };
  if (q.cadence !== undefined && q.cadence !== "") {
    if (!(SOURCE_CADENCES as readonly string[]).includes(q.cadence)) return `cadence must be one of ${SOURCE_CADENCES.join(", ")}`;
    req.cadence = q.cadence;
  }
  if (q.expiry !== undefined && q.expiry !== "") {
    if (!(SOURCE_EXPIRIES as readonly string[]).includes(q.expiry)) return `expiry must be one of ${SOURCE_EXPIRIES.join(", ")}`;
    req.expiry = q.expiry;
  }
  return req;
}

export function registerSourcesRoutes(app: FastifyInstance): void {
  app.get("/api/sources", async (): Promise<BridgedResult<SourcesSummary>> => {
    const read = await readSources();
    if (!read.ok || read.data === null) return { ok: false, data: null, error: read.error };
    const open = backtestResultsOn(await getFeatures());
    // Fail closed: a row whose licence is not plainly false is a vendor row.
    const shown = read.data.sources.filter((s) => open || s.licensed === false);
    return {
      ok: true,
      data: { ...read.data, sources: shown, withheld: read.data.sources.length - shown.length },
      error: null,
    };
  });

  app.get<{ Querystring: CoverageQuery }>(
    "/api/sources/coverage",
    async (req, reply): Promise<BridgedResult<SourceCoverage> | FastifyReply> => {
      const parsed = parseCoverage(req.query);
      if (typeof parsed === "string") return reply.code(400).send({ error: parsed });
      if (parsed.licensed && !backtestOn(await getFeatures())) {
        return reply.code(403).send({ error: "the vendor's sources need the backtest feature on" });
      }
      return readCoverage(parsed);
    },
  );
}
