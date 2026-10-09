import { describe, expect, it } from "vitest";
// @ts-expect-error -- a plain .mjs launcher helper, no type declarations
import { sandboxProblem } from "../scripts/sandbox.mjs";

const BIN = "/repo/node_modules/electron/dist/electron";
const HELPER = "/repo/node_modules/electron/dist/chrome-sandbox";

function host(restrict: string | null, helper: { uid: number; mode: number } | null) {
  return {
    platform: "linux",
    electronBinary: BIN,
    readFile: () => {
      if (restrict === null) throw new Error("ENOENT");
      return `${restrict}\n`;
    },
    stat: (p: string) => {
      if (p !== HELPER || helper === null) throw new Error("ENOENT");
      return helper;
    },
  };
}

describe("sandboxProblem (2026-10-08 OS audit: Ubuntu's AppArmor userns restriction)", () => {
  it("names the one-time fix when the restriction is on and the helper is not setuid root", () => {
    const msg = sandboxProblem(host("1", { uid: 1000, mode: 0o100755 }));
    expect(msg).toContain(`sudo chown root:root "${HELPER}" && sudo chmod 4755 "${HELPER}"`);
    expect(msg).toContain("http://127.0.0.1:5070");
  });

  it("is quiet once the helper is root-owned and setuid", () => {
    expect(sandboxProblem(host("1", { uid: 0, mode: 0o104755 }))).toBeNull();
  });

  it("is quiet where the restriction is off, absent, or the platform is not Linux", () => {
    expect(sandboxProblem(host("0", { uid: 1000, mode: 0o100755 }))).toBeNull();
    expect(sandboxProblem(host(null, { uid: 1000, mode: 0o100755 }))).toBeNull();
    expect(sandboxProblem({ ...host("1", { uid: 1000, mode: 0o100755 }), platform: "darwin" })).toBeNull();
  });

  it("a root-owned helper without the setuid bit still needs the fix", () => {
    expect(sandboxProblem(host("1", { uid: 0, mode: 0o100755 }))).not.toBeNull();
  });
});
