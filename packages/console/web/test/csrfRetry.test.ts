import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * A page left open across a console restart holds the old CSRF token. `csrfFetch` fetches a fresh one and
 * sends the write once more on that refusal alone; any other answer, a failure included, comes back as it
 * was. Shown to fail with the retry removed ("a stale token is fetched again").
 */

function server(tokens: string[], answer: (token: string) => Response) {
  const writes: string[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/csrf") return new Response(JSON.stringify({ token: tokens.shift() }), { status: 200 });
    const token = (init?.headers as Record<string, string>)["x-csrf-token"]!;
    writes.push(token);
    return answer(token);
  });
  vi.stubGlobal("fetch", fetchMock);
  return writes;
}

const refused = () => new Response(JSON.stringify({ error: "missing or invalid csrf token" }), { status: 403 });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("a write with the page's CSRF token", () => {
  it("a stale token is fetched again and the write sent once more", async () => {
    const writes = server(["old", "new"], (t) => (t === "new" ? new Response("{}", { status: 200 }) : refused()));
    const { csrfFetch } = await import("../src/lib/api");
    const res = await csrfFetch("/api/x", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
    expect(res.status).toBe(200);
    expect(writes).toEqual(["old", "new"]);
  });

  it("another refusal or a failure is returned as it came, never sent twice", async () => {
    const writes = server(["t"], () => new Response(JSON.stringify({ error: "forbidden origin" }), { status: 403 }));
    const { csrfFetch } = await import("../src/lib/api");
    expect((await csrfFetch("/api/x", { method: "POST" })).status).toBe(403);
    expect(writes).toEqual(["t"]);
  });

  it("retries once at most", async () => {
    const writes = server(["a", "b", "c"], () => refused());
    const { csrfFetch } = await import("../src/lib/api");
    expect((await csrfFetch("/api/x", { method: "POST" })).status).toBe(403);
    expect(writes).toEqual(["a", "b"]);
  });
});
