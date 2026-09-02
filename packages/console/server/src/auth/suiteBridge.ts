import { spawnSync } from "node:child_process";

/**
 * Read the suite's canonical broker OAuth entries — the ones the Python side
 * owns (`production:client_secret` / `production:refresh_token` under the
 * `cherrypick-broker` service). Python's keyring writes Windows targets as
 * `user@service`, which the Node keyring cannot address, so this bridges
 * through Python itself (a suite prerequisite already). Read-only: this
 * module NEVER writes or deletes those entries — they belong to the suite.
 * Called once per process and cached by the caller.
 */
export function readSuiteOauthEntries(): { clientSecret: string; refreshToken: string } | null {
  const script = [
    "import keyring, json",
    "cs = keyring.get_password('cherrypick-broker', 'production:client_secret')",
    "rt = keyring.get_password('cherrypick-broker', 'production:refresh_token')",
    "print(json.dumps({'cs': cs, 'rt': rt}))",
  ].join("\n");
  try {
    const out = spawnSync("python", ["-c", script], { encoding: "utf-8", timeout: 15_000, windowsHide: true });
    if (out.status !== 0) return null;
    const parsed = JSON.parse(out.stdout.trim()) as { cs?: string | null; rt?: string | null };
    if (typeof parsed.cs !== "string" || parsed.cs === "" || typeof parsed.rt !== "string" || parsed.rt === "") {
      return null;
    }
    return { clientSecret: parsed.cs, refreshToken: parsed.rt };
  } catch {
    return null;
  }
}
