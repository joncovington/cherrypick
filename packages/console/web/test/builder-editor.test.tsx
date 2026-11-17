import { describe, it, expect, vi, afterEach } from "vitest";
import { editorText } from "../src/pages/Builder/BuilderPage";
import { builderDraft, builderSave, builderToday } from "../src/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the Builder's JSON view", () => {
  it("never shows the name, the register label or the switch in the text", () => {
    const text = editorText({ name: "x", register: { item: 75 }, enabled: true, symbol: "SPX", legs: [] });
    expect(JSON.parse(text)).toEqual({ symbol: "SPX", legs: [] });
  });
});

describe("the editor's calls", () => {
  function stub(status: number, body: unknown) {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/csrf") return new Response(JSON.stringify({ token: "t" }), { status: 200 });
      return new Response(JSON.stringify(body), { status });
    });
    return calls;
  }

  it("hands back the module's problems from a refused save rather than throwing", async () => {
    const calls = stub(422, { error: "my-fly: missing 'legs'", code: "invalid", problems: ["my-fly: missing 'legs'"] });
    const r = await builderSave("my-fly", { symbol: "SPX" });
    expect(r).toMatchObject({ ok: false, code: "invalid", problems: ["my-fly: missing 'legs'"] });
    const sent = calls.find((c) => c.url === "/api/automations/builder/save")!;
    expect(JSON.parse(String(sent.init?.body))).toEqual({ name: "my-fly", automation: { symbol: "SPX" } });
  });

  it("asks for the editable spec by name alone", async () => {
    const calls = stub(200, { ok: true, name: "iron-fly-1000", automation: { symbol: "SPX" } });
    const r = await builderDraft("iron-fly-1000");
    expect(r.automation).toEqual({ symbol: "SPX" });
    const sent = calls.find((c) => c.url === "/api/automations/builder/draft")!;
    expect(JSON.parse(String(sent.init?.body))).toEqual({ name: "iron-fly-1000" });
  });

  it("asks for today's decision by name, or with the unsaved edit", async () => {
    const calls = stub(200, { name: "mine", state: "would_skip" });
    expect((await builderToday("mine")).state).toBe("would_skip");
    await builderToday("mine", { symbol: "SPX" });
    const sent = calls.filter((c) => c.url === "/api/automations/builder/today").map((c) => JSON.parse(String(c.init?.body)));
    expect(sent).toEqual([{ name: "mine" }, { name: "mine", automation: { symbol: "SPX" } }]);
  });

  it("names the status when a refusal has no body", async () => {
    vi.stubGlobal("fetch", async (url: string) =>
      url === "/api/csrf" ? new Response(JSON.stringify({ token: "t" })) : new Response("", { status: 502 }),
    );
    expect(await builderDraft("x", {})).toMatchObject({ ok: false, error: "HTTP 502" });
  });
});
