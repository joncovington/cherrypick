import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { suitePython } from "@console/shared/paths";
import { pythonArgv } from "../src/services/moduleCli.js";

/**
 * The bridges' `-m` reads go through `server/python/unthrottled.py` on Windows, so a module CLI the
 * console spawns is not a power-throttled background process. The wrapper must be invisible to the
 * module: the same argv, the same cwd-first import path, the same exit status as `python -m`.
 */

const PROBE = `
import json, os, sys
import sibling  # resolvable only with the working directory first on sys.path, as under -m
state = None
if os.name == "nt":
    import ctypes, ctypes.wintypes as wt
    class S(ctypes.Structure):
        _fields_ = [("Version", wt.ULONG), ("ControlMask", wt.ULONG), ("StateMask", wt.ULONG)]
    k = ctypes.WinDLL("kernel32")
    k.GetCurrentProcess.restype = wt.HANDLE
    k.GetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    s = S(1, 0, 0)
    k.GetProcessInformation(k.GetCurrentProcess(), 4, ctypes.byref(s), ctypes.sizeof(s))
    state = [s.ControlMask, s.StateMask]
print(json.dumps({"argv": sys.argv[1:], "main": __name__, "sibling": sibling.VALUE, "state": state}))
sys.exit(3)
`;

function probeDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "console-unthrottled-"));
  fs.writeFileSync(path.join(dir, "probe_mod.py"), PROBE);
  fs.writeFileSync(path.join(dir, "sibling.py"), "VALUE = 7\n");
  return dir;
}

function run(argv: string[], cwd: string) {
  const out = spawnSync(suitePython(), argv, { cwd, encoding: "utf-8", timeout: 30_000, windowsHide: true });
  return { status: out.status, doc: JSON.parse(out.stdout.trim()) as Record<string, unknown>, stderr: out.stderr };
}

const python = spawnSync(suitePython(), ["-c", "print(1)"], { encoding: "utf-8" });
const hasPython = python.status === 0;

describe("pythonArgv", () => {
  it("routes a -m read through the wrapper on Windows only, and leaves anything else alone", () => {
    expect(pythonArgv(["-m", "x", "a"], "linux")).toEqual(["-m", "x", "a"]);
    expect(pythonArgv(["-c", "print(1)"], "win32")).toEqual(["-c", "print(1)"]);
    const wrapped = pythonArgv(["-m", "x", "a"], "win32");
    expect(path.basename(wrapped[0]!)).toBe("unthrottled.py");
    expect(wrapped.slice(1)).toEqual(["-m", "x", "a"]);
  });
});

describe.skipIf(!hasPython)("unthrottled.py", () => {
  it("runs the module as python -m does: argv, cwd-first imports, __main__ and the exit status", () => {
    const dir = probeDir();
    const plain = run(["-m", "probe_mod", "--flag", "two words"], dir);
    const wrapped = run(pythonArgv(["-m", "probe_mod", "--flag", "two words"], "win32"), dir);
    expect(plain.status).toBe(3);
    expect(wrapped.status).toBe(3);
    const { state: _p, ...plainDoc } = plain.doc;
    const { state: _w, ...wrappedDoc } = wrapped.doc;
    expect(wrappedDoc).toEqual(plainDoc);
    expect(wrappedDoc).toEqual({ argv: ["--flag", "two words"], main: "__main__", sibling: 7 });
  });

  it.skipIf(process.platform !== "win32")("leaves the module's process opted out of power throttling", () => {
    const dir = probeDir();
    expect(run(["-m", "probe_mod"], dir).doc["state"]).toEqual([0, 0]);
    expect(run(pythonArgv(["-m", "probe_mod"]), dir).doc["state"]).toEqual([1, 0]);
  });
});
