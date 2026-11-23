import { afterEach, describe, it, expect, vi } from "vitest";
import { getJson } from "../src/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getJson", () => {
  it("returns the parsed body of a 2xx answer", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ a: 1 }), { status: 200 })));
    expect(await getJson<{ a: number }>("/api/x")).toEqual({ a: 1 });
  });

  it("throws the url and the status on a non-2xx answer", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 503 })));
    await expect(getJson("/api/x?y=1")).rejects.toThrow("/api/x?y=1: HTTP 503");
  });
});
