import path from "node:path";
import { describe, expect, it } from "vitest";
import { suitePython } from "@console/shared";

// 2026-10-08 OS audit: every bridge spawned a bare `python`, which failed a console started by hand on
// Linux (often only `python3`, and the venv on nobody's PATH).
const ROOT = path.resolve("/repo");
const START = path.join(ROOT, "packages", "console", "shared", "dist");
const RUN_PY = path.join(ROOT, "packages", "orchestrator", "run.py");

function fs(...present: string[]) {
  const set = new Set(present);
  return (p: string) => set.has(p);
}

describe("suitePython", () => {
  it("prefers the interpreter the launcher named", () => {
    expect(suitePython({ CHERRYPICK_PYTHON: "/opt/x/python" }, START, fs())).toBe("/opt/x/python");
  });

  it("finds the checkout's POSIX venv by walking up to run.py", () => {
    const venv = path.join(ROOT, ".venv", "bin", "python");
    expect(suitePython({}, START, fs(RUN_PY, venv))).toBe(venv);
  });

  it("finds the checkout's Windows venv", () => {
    const venv = path.join(ROOT, ".venv", "Scripts", "python.exe");
    expect(suitePython({}, START, fs(RUN_PY, venv))).toBe(venv);
  });

  it("falls back to python on PATH without a venv or outside a checkout", () => {
    expect(suitePython({}, START, fs(RUN_PY))).toBe("python");
    expect(suitePython({}, START, fs())).toBe("python");
  });
});
