import type { FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../config.js";
import { readReview, type ReviewPayload } from "../readers/review.js";

export function registerReviewRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { session?: string } }>(
    "/api/review",
    async (req): Promise<ReviewPayload> => readReview(config, req.query.session),
  );
}
