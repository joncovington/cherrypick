import Fastify, { type FastifyInstance } from "fastify";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { registerSecurity } from "../src/security.js";

/**
 * The WebSocket upgrade is a GET, so the mutating-route checks never see it, and browsers do not
 * apply the same-origin policy to sockets. A plain GET route stands in for /ws: the hook under test
 * runs on the request before any handler, and keys on the URL.
 */
let app: FastifyInstance;

beforeEach(async () => {
  app = Fastify();
  registerSecurity(app);
  app.get("/ws", async () => ({ upgraded: true }));
  app.get("/api/elsewhere", async () => ({ ok: true }));
  await app.ready();
});

afterEach(async () => {
  await app.close();
});

const get = (url: string, origin?: string) =>
  app.inject({
    method: "GET",
    url,
    headers: origin === undefined ? { host: "127.0.0.1:5070" } : { host: "127.0.0.1:5070", origin },
  });

describe("/ws origin", () => {
  it("refuses a cross-site page", async () => {
    expect((await get("/ws", "https://evil.example")).statusCode).toBe(403);
    expect((await get("/ws?x=1", "https://evil.example")).statusCode).toBe(403);
  });

  it("accepts the console's own origin and a client that sends none", async () => {
    expect((await get("/ws", "http://127.0.0.1:5070")).statusCode).toBe(200);
    expect((await get("/ws", "http://localhost:5070")).statusCode).toBe(200);
    expect((await get("/ws")).statusCode).toBe(200);
  });

  it("leaves other GET routes alone", async () => {
    expect((await get("/api/elsewhere", "https://evil.example")).statusCode).toBe(200);
  });
});
