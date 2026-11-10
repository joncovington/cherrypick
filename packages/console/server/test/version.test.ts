import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import {
  fetchLatestRelease,
  isNewer,
  parseDescribe,
  readArchival,
  readVersion,
  resetVersionCaches,
  settleVersionCheck,
  updateCheckEnabled,
} from "../src/readers/version.js";

/**
 * The status bar's version chip. Pinned: how `git describe` and a release ZIP's archival file
 * become a tag, that "update available" means strictly newer by number (never by string, never
 * when either side is unreadable), that `update_check: false` sends no request at all, and that a
 * failed request is reported rather than thrown.
 */

let home: string;
let root: string;
let config: ConsoleConfig;

beforeEach(() => {
  resetVersionCaches();
  home = fs.mkdtempSync(path.join(os.tmpdir(), "cp-version-home-"));
  root = fs.mkdtempSync(path.join(os.tmpdir(), "cp-version-root-"));
  fs.mkdirSync(path.join(home, "config"), { recursive: true });
  config = { port: 0, paths: { cherrypick: home } } as unknown as ConsoleConfig;
});

function release(tag: string) {
  const calls: string[] = [];
  const fetcher = async (url: string) => {
    calls.push(url);
    return { ok: true, status: 200, json: async () => ({ tag_name: tag, name: `${tag} — a name`, html_url: `https://example.test/${tag}`, published_at: "2026-11-09T00:00:00Z" }) };
  };
  return { calls, fetcher };
}

describe("parseDescribe", () => {
  it("reads a release, a checkout past one, and a dirty tree", () => {
    expect(parseDescribe("v0.12.0")).toEqual({ tag: "v0.12.0", commitsPast: 0 });
    expect(parseDescribe("v0.11.2-1307-g8d4f772dc")).toEqual({ tag: "v0.11.2", commitsPast: 1307 });
    expect(parseDescribe("v0.11.2-3-gabc1234-dirty")).toEqual({ tag: "v0.11.2", commitsPast: 3 });
  });
  it("gives no tag for anything else", () => {
    expect(parseDescribe("8d4f772dc")).toEqual({ tag: null, commitsPast: null });
  });
});

describe("isNewer", () => {
  it("compares by number, not by string", () => {
    expect(isNewer("v0.12.0", "v0.11.2")).toBe(true);
    expect(isNewer("v0.10.0", "v0.9.0")).toBe(true);
    expect(isNewer("v0.11.2", "v0.11.2")).toBe(false);
    expect(isNewer("v0.11.1", "v0.11.2")).toBe(false);
    expect(isNewer("v1.0.0", "v0.99.99")).toBe(true);
  });
  it("puts a release after its own betas, and a beta after the release before it", () => {
    expect(parseDescribe("v0.12.1-beta")).toEqual({ tag: "v0.12.1-beta", commitsPast: 0 });
    expect(parseDescribe("v0.12.1-beta.2-4-gabc1234")).toEqual({ tag: "v0.12.1-beta.2", commitsPast: 4 });
    expect(isNewer("v0.12.1", "v0.12.1-beta")).toBe(true);
    expect(isNewer("v0.12.1-beta.2", "v0.12.1-beta")).toBe(true);
    expect(isNewer("v0.12.0", "v0.12.1-beta")).toBe(false);
    expect(isNewer("v0.12.1-beta", "v0.12.0")).toBe(true);
  });
  it("is false when either side is unreadable", () => {
    expect(isNewer("nightly", "v0.11.2")).toBe(false);
    expect(isNewer("v0.12.1-rc1", "v0.11.2")).toBe(false);
    expect(isNewer("v0.12.0", "8d4f772dc")).toBe(false);
  });
});

describe("readArchival", () => {
  it("reads a filled-in file and ignores the unfilled template", () => {
    expect(readArchival("describe: v0.12.0\n")).toBe("v0.12.0");
    expect(readArchival("describe: $Format:%(describe:tags=true,match=v[0-9]*)$\n")).toBeNull();
    expect(readArchival("describe: %(describe:tags=true)\n")).toBeNull();
    expect(readArchival("")).toBeNull();
  });
});

describe("readVersion", () => {
  it("reads the installed version from a release ZIP's archival file", async () => {
    fs.writeFileSync(path.join(root, ".git_archival.txt"), "describe: v0.11.2\n");
    const { fetcher } = release("v0.11.2");
    const v = await readVersion(config, { root, fetcher });
    expect(v.installed).toEqual({ describe: "v0.11.2", tag: "v0.11.2", commitsPast: 0, source: "archive" });
  });

  it("announces a newer release once the background check lands", async () => {
    fs.writeFileSync(path.join(root, ".git_archival.txt"), "describe: v0.11.2\n");
    const { calls, fetcher } = release("v0.12.0");
    const first = await readVersion(config, { root, fetcher });
    expect(first.updateAvailable).toBe(false); // the check runs in the background, never awaited
    await settleVersionCheck();
    const second = await readVersion(config, { root, fetcher });
    expect(second.updateAvailable).toBe(true);
    expect(second.check.latestTag).toBe("v0.12.0");
    expect(second.check.latestUrl).toBe("https://example.test/v0.12.0");
    expect(calls).toHaveLength(1); // cached: a second poll does not ask again
    expect(calls[0]).toContain("/releases/latest");
  });

  it("says nothing is newer on the latest release, or past it", async () => {
    fs.writeFileSync(path.join(root, ".git_archival.txt"), "describe: v0.12.0-14-gabc1234\n");
    const { fetcher } = release("v0.12.0");
    await readVersion(config, { root, fetcher });
    await settleVersionCheck();
    const v = await readVersion(config, { root, fetcher });
    expect(v.installed.commitsPast).toBe(14);
    expect(v.updateAvailable).toBe(false);
  });

  it("sends no request at all with update_check: false", async () => {
    fs.writeFileSync(path.join(home, "config", "console.json"), JSON.stringify({ update_check: false }));
    expect(updateCheckEnabled(home)).toBe(false);
    const { calls, fetcher } = release("v9.9.9");
    const v = await readVersion(config, { root, fetcher });
    await settleVersionCheck();
    expect(calls).toHaveLength(0);
    expect(v.check.enabled).toBe(false);
    expect(v.updateAvailable).toBe(false);
  });

  it("is on with no console.json, and with a malformed one", () => {
    expect(updateCheckEnabled(home)).toBe(true);
    fs.writeFileSync(path.join(home, "config", "console.json"), "{not json");
    expect(updateCheckEnabled(home)).toBe(true);
  });
});

describe("fetchLatestRelease", () => {
  it("reports an HTTP failure and a thrown request instead of throwing", async () => {
    const notFound = await fetchLatestRelease(async () => ({ ok: false, status: 404, json: async () => ({}) }));
    expect(notFound.latestTag).toBeNull();
    expect(notFound.error).toContain("404");
    const offline = await fetchLatestRelease(async () => {
      throw new Error("getaddrinfo ENOTFOUND api.github.com");
    });
    expect(offline.error).toContain("ENOTFOUND");
  });
});
