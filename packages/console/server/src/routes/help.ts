import type { FastifyInstance } from "fastify";
import type { HelpIndexPayload, HelpPagePayload } from "@console/shared";
import { readHelpImage, readHelpIndex, readHelpPage } from "../readers/help.js";

/** The Help page: the guide index, one page by slug, and the guides' images. */
export function registerHelpRoutes(app: FastifyInstance): void {
  app.get("/api/help/index", async (): Promise<HelpIndexPayload> => readHelpIndex());
  app.get<{ Params: { slug: string } }>("/api/help/page/:slug", async (req, reply): Promise<HelpPagePayload | { error: string }> => {
    const page = readHelpPage(req.params.slug);
    if (page === null) return reply.code(404).send({ error: `no guide page "${req.params.slug}"` });
    return page;
  });
  app.get<{ Params: { name: string } }>("/api/help/images/:name", async (req, reply) => {
    const img = readHelpImage(req.params.name);
    if (img === null) return reply.code(404).send({ error: "no such image" });
    return reply.type(img.type).header("cache-control", "no-cache").send(img.body);
  });
}
