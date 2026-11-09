/**
 * The suite version this checkout holds, and whether a newer release is out (`/api/version`).
 *
 * Installed version: `git describe --tags` in a git checkout. A release ZIP has no `.git`, so
 * there it is `.git_archival.txt` at the repo root, which GitHub fills in from `git describe` when
 * it builds the archive (`export-subst` in `.gitattributes`). A file still holding `$Format:` was
 * not built that way, and counts as unknown.
 *
 * Latest release: GitHub's `releases/latest` for the suite's repo, the same release the install
 * docs point at. This is the console's one request to the internet. It carries no data of the
 * user's (a generic User-Agent, no token), runs at most every CHECK_TTL_MS, and is off when
 * `update_check: false` is set in console.json. A failure is shown as such and never blocks a page:
 * the route serves the cached answer and refreshes it in the background.
 */

import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import type { VersionPayload } from "@console/shared";
import { consoleConfigPath } from "@console/shared/paths";
import { REPO_ROOT, type ConsoleConfig } from "../config.js";

export const RELEASES_REPO = "joncovington/cherrypick";
const CHECK_TTL_MS = 6 * 3_600_000;
const RETRY_AFTER_FAILURE_MS = 3_600_000;
const INSTALLED_TTL_MS = 60_000;

// --------------------------------------------------------------------------- pure helpers

/** `v0.12.0` / `v0.12.0-14-gabc1234` / `v0.12.0-14-gabc1234-dirty` → tag and commits past it. */
export function parseDescribe(describe: string): { tag: string | null; commitsPast: number | null } {
  const s = describe.trim().replace(/-dirty$/, "");
  const past = /^(v\d+\.\d+\.\d+)-(\d+)-g[0-9a-f]+$/.exec(s);
  if (past) return { tag: past[1]!, commitsPast: Number(past[2]) };
  if (/^v\d+\.\d+\.\d+$/.test(s)) return { tag: s, commitsPast: 0 };
  return { tag: null, commitsPast: null };
}

function parts(tag: string): [number, number, number] | null {
  const m = /^v?(\d+)\.(\d+)\.(\d+)$/.exec(tag.trim());
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** True when `latest` is a strictly newer vMAJOR.MINOR.PATCH than `installed`; false if either is unreadable. */
export function isNewer(latest: string, installed: string): boolean {
  const a = parts(latest);
  const b = parts(installed);
  if (a === null || b === null) return false;
  for (let i = 0; i < 3; i++) {
    if (a[i]! !== b[i]!) return a[i]! > b[i]!;
  }
  return false;
}

/** The archive's describe line, or null when the file is absent or was never substituted. */
export function readArchival(text: string): string | null {
  const m = /^describe:\s*(.+)$/m.exec(text);
  const v = m?.[1]?.trim() ?? "";
  return v === "" || v.includes("$Format") || v.includes("%(") ? null : v;
}

/** `update_check` in console.json: anything but a literal false means on. */
export function updateCheckEnabled(home: string): boolean {
  try {
    const raw = JSON.parse(fs.readFileSync(consoleConfigPath(home), "utf-8")) as Record<string, unknown>;
    return raw["update_check"] !== false;
  } catch {
    return true;
  }
}

// --------------------------------------------------------------------------- installed

type Installed = VersionPayload["installed"];
let installedCache: { at: number; value: Installed } | null = null;

function gitDescribe(root: string): Promise<string | null> {
  return new Promise((resolve) => {
    execFile(
      "git",
      ["describe", "--tags", "--match", "v[0-9]*", "--dirty"],
      { cwd: root, timeout: 10_000, windowsHide: true },
      (err, stdout) => resolve(err ? null : stdout.trim() || null),
    );
  });
}

export async function readInstalled(root = REPO_ROOT): Promise<Installed> {
  if (installedCache !== null && Date.now() - installedCache.at < INSTALLED_TTL_MS) return installedCache.value;
  let describe: string | null = null;
  let source: Installed["source"] = null;
  if (fs.existsSync(path.join(root, ".git"))) {
    describe = await gitDescribe(root);
    if (describe !== null) source = "git";
  }
  if (describe === null) {
    try {
      describe = readArchival(fs.readFileSync(path.join(root, ".git_archival.txt"), "utf-8"));
      if (describe !== null) source = "archive";
    } catch {
      describe = null;
    }
  }
  const value: Installed = { describe, source, ...(describe === null ? { tag: null, commitsPast: null } : parseDescribe(describe)) };
  installedCache = { at: Date.now(), value };
  return value;
}

// --------------------------------------------------------------------------- latest release

type Check = Omit<VersionPayload["check"], "enabled">;
type Fetcher = (url: string, init: { headers: Record<string, string>; signal: AbortSignal }) => Promise<{
  ok: boolean;
  status: number;
  json: () => Promise<unknown>;
}>;

let checkCache: { at: number; value: Check } | null = null;
let checkRun: Promise<void> | null = null;

export async function fetchLatestRelease(fetcher: Fetcher = fetch as unknown as Fetcher): Promise<Check> {
  const checkedAt = new Date().toISOString();
  const empty = { latestTag: null, latestName: null, latestUrl: null, latestPublishedAt: null };
  try {
    const res = await fetcher(`https://api.github.com/repos/${RELEASES_REPO}/releases/latest`, {
      headers: { Accept: "application/vnd.github+json", "User-Agent": "cherrypick-console" },
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) return { ...empty, checkedAt, error: `GitHub answered HTTP ${String(res.status)}` };
    const j = (await res.json()) as Record<string, unknown>;
    const tag = typeof j["tag_name"] === "string" ? j["tag_name"] : null;
    if (tag === null) return { ...empty, checkedAt, error: "GitHub's answer named no release tag" };
    return {
      latestTag: tag,
      latestName: typeof j["name"] === "string" ? j["name"] : null,
      latestUrl: typeof j["html_url"] === "string" ? j["html_url"] : null,
      latestPublishedAt: typeof j["published_at"] === "string" ? j["published_at"] : null,
      checkedAt,
      error: null,
    };
  } catch (err) {
    return { ...empty, checkedAt, error: err instanceof Error ? err.message.split("\n")[0] ?? "request failed" : "request failed" };
  }
}

/** The cached answer; a stale (or missing) one is refreshed in the background, never awaited. */
function latestRelease(fetcher?: Fetcher): Check | null {
  const ttl = checkCache?.value.error ? RETRY_AFTER_FAILURE_MS : CHECK_TTL_MS;
  if ((checkCache === null || Date.now() - checkCache.at > ttl) && checkRun === null) {
    checkRun = fetchLatestRelease(fetcher)
      .then((value) => {
        // A failure keeps the last good answer's tag, so a flaky network does not hide a known update.
        checkCache = {
          at: Date.now(),
          value: value.error !== null && checkCache !== null && checkCache.value.latestTag !== null
            ? { ...checkCache.value, checkedAt: value.checkedAt, error: value.error }
            : value,
        };
      })
      .finally(() => {
        checkRun = null;
      });
  }
  return checkCache?.value ?? null;
}

export async function readVersion(config: ConsoleConfig, opts: { root?: string; fetcher?: Fetcher } = {}): Promise<VersionPayload> {
  const installed = await readInstalled(opts.root);
  const enabled = updateCheckEnabled(config.paths.cherrypick);
  const latest = enabled ? latestRelease(opts.fetcher) : null;
  const check: VersionPayload["check"] = {
    enabled,
    latestTag: latest?.latestTag ?? null,
    latestName: latest?.latestName ?? null,
    latestUrl: latest?.latestUrl ?? null,
    latestPublishedAt: latest?.latestPublishedAt ?? null,
    checkedAt: latest?.checkedAt ?? null,
    error: latest?.error ?? null,
  };
  return {
    installed,
    check,
    updateAvailable: installed.tag !== null && check.latestTag !== null && isNewer(check.latestTag, installed.tag),
  };
}

/** Tests only: forget both caches. */
export function resetVersionCaches(): void {
  installedCache = null;
  checkCache = null;
  checkRun = null;
}

/** Tests only: wait for a background check started by `readVersion`. */
export async function settleVersionCheck(): Promise<void> {
  await checkRun;
}
