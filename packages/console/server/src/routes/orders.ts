import crypto from "node:crypto";
import type { FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../config.js";
import { listStaged, insertStaged, deleteStaged } from "../store/consoleDb.js";
import { dryRunOrder, type TicketLeg } from "../services/staging.js";

function parseTicketLegs(raw: unknown): TicketLeg[] | null {
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const legs: TicketLeg[] = [];
  for (const item of raw) {
    const l = item as Record<string, unknown>;
    const symbol = l["symbol"];
    const quantity = Number(l["quantity"]);
    const price = Number(l["price"]);
    if (typeof symbol !== "string" || symbol === "") return null;
    if (!Number.isFinite(quantity) || quantity === 0 || !Number.isFinite(price)) return null;
    legs.push({ symbol, quantity, price });
  }
  return legs;
}

export function registerOrderRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get("/api/orders/staged", async () => ({ tickets: listStaged(config) }));

  // Button-triggered only (mutating POST behind the CSRF gate) — never on a
  // timer or page load. Always saves, even when the dry-run itself failed.
  app.post("/api/orders/stage", async (req, reply) => {
    const body = req.body as Record<string, unknown>;
    const legs = parseTicketLegs(body["legs"]);
    const symbol = typeof body["symbol"] === "string" ? body["symbol"].toUpperCase() : "";
    if (legs === null || symbol === "") {
      return reply.code(400).send({ error: "symbol and legs [{symbol, quantity, price}] required" });
    }
    const dryRun = await dryRunOrder(legs);
    const ticket = insertStaged(config, {
      id: crypto.randomUUID(),
      symbol,
      strategy: typeof body["strategy"] === "string" ? body["strategy"] : null,
      legs,
      credit: typeof body["credit"] === "number" ? body["credit"] : null,
      maxRisk: typeof body["maxRisk"] === "number" ? body["maxRisk"] : null,
      dryRun,
      note: typeof body["note"] === "string" ? body["note"] : null,
    });
    return { ticket };
  });

  app.delete("/api/orders/staged/:id", async (req) => {
    const { id } = req.params as { id: string };
    return { deleted: deleteStaged(config, id) };
  });
}
