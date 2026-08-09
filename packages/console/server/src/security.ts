import crypto from "node:crypto";
import type { FastifyInstance, FastifyRequest, FastifyReply } from "fastify";

/**
 * The suite's mutating-surface posture (ported from scout/settings_serve):
 * loopback binding alone is not enough once a route writes anything — a
 * malicious webpage can fetch http://127.0.0.1:<port>, and DNS rebinding
 * defeats same-origin. Every request must present a loopback Host; every
 * mutating request additionally needs the per-process CSRF token, a JSON
 * content type, and a loopback Origin when one is present.
 */
export const CSRF_TOKEN = crypto.randomBytes(24).toString("hex");

const LOOPBACK_HOSTS = /^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/;

function isLoopbackUrl(value: string): boolean {
  try {
    return LOOPBACK_HOSTS.test(new URL(value).host);
  } catch {
    return false;
  }
}

export function registerSecurity(app: FastifyInstance): void {
  app.addHook("onRequest", async (req: FastifyRequest, reply: FastifyReply) => {
    const host = req.headers.host ?? "";
    if (!LOOPBACK_HOSTS.test(host)) {
      return reply.code(403).send({ error: "forbidden host" });
    }
    if (req.method === "GET" || req.method === "HEAD" || req.method === "OPTIONS") return;

    const origin = req.headers.origin;
    if (origin !== undefined && !isLoopbackUrl(origin)) {
      return reply.code(403).send({ error: "forbidden origin" });
    }
    if (req.headers["x-csrf-token"] !== CSRF_TOKEN) {
      return reply.code(403).send({ error: "missing or invalid csrf token" });
    }
    const ct = req.headers["content-type"] ?? "";
    if (req.method !== "DELETE" && !ct.includes("application/json")) {
      return reply.code(415).send({ error: "json only" });
    }
  });

  // The SPA fetches the token once per session; Host-checked like everything else.
  app.get("/api/csrf", async () => ({ token: CSRF_TOKEN }));
}
