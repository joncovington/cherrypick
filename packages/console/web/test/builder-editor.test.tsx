import { describe, it, expect, vi, afterEach } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BuilderAutomation } from "@console/shared";
import { BuilderCard, BuilderEditor, editorText } from "../src/pages/Builder/BuilderPage";
import { builderDraft, builderSave } from "../src/lib/api";

const html = (el: React.ReactElement) =>
  renderToString(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>).replace(/<!--\s*-->/g, "");

const A: BuilderAutomation = {
  name: "iron-fly-1000",
  group: "ensemble",
  symbol: "SPX",
  enabled: true,
  note: null,
  start: null,
  register: null,
  summary: "",
  blocks: [],
  forward: null,
  suggestion: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the Builder's editor", () => {
  it("is opened from a card by Edit or Copy as new", () => {
    const out = html(<BuilderCard a={A} />);
    expect(out).toContain(">Edit</button>");
    expect(out).toContain(">Copy as new</button>");
  });

  it("never shows the name, the register label or the switch in the text", () => {
    const text = editorText({ name: "x", register: { item: 75 }, enabled: true, symbol: "SPX", legs: [] });
    expect(JSON.parse(text)).toEqual({ symbol: "SPX", legs: [] });
  });

  it("keeps the name for an edit and offers a new one for a copy, and says a save is off", () => {
    const edit = html(<BuilderEditor source="iron-fly-1000" copy={false} onClose={() => undefined} />);
    expect(edit).toContain('<input disabled="" aria-label="automation name" value="iron-fly-1000"/>');
    expect(edit).toContain("A save writes this automation <strong>off</strong>");
    expect(edit).toContain(">Save (off)</button>");
    const copy = html(<BuilderEditor source="iron-fly-1000" copy={true} onClose={() => undefined} />);
    expect(copy).toContain('<input aria-label="automation name" value="iron-fly-1000-copy"/>');
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

  it("names the status when a refusal has no body", async () => {
    vi.stubGlobal("fetch", async (url: string) =>
      url === "/api/csrf" ? new Response(JSON.stringify({ token: "t" })) : new Response("", { status: 502 }),
    );
    expect(await builderDraft("x", {})).toMatchObject({ ok: false, error: "HTTP 502" });
  });
});
