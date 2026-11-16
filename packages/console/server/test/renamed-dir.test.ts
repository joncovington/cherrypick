import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { renamedDir } from "../src/config.js";

describe("renamedDir", () => {
  it("reads the old folder only while it alone exists", () => {
    const data = fs.mkdtempSync(path.join(os.tmpdir(), "renamed-dir-"));
    expect(renamedDir(data, "automations", "ensemble")).toBe(path.join(data, "automations"));
    fs.mkdirSync(path.join(data, "ensemble"));
    expect(renamedDir(data, "automations", "ensemble")).toBe(path.join(data, "ensemble"));
    fs.mkdirSync(path.join(data, "automations"));
    expect(renamedDir(data, "automations", "ensemble")).toBe(path.join(data, "automations"));
    fs.rmSync(data, { recursive: true, force: true });
  });
});
