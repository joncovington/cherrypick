import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { GUIDE_DIR, parseGuideIndex, readHelpImage, readHelpIndex, readHelpPage } from "../src/readers/help.js";

/**
 * The Help page's files. Pinned: the index is parsed into groups in order, a slug the index does not
 * list is never served (so the route cannot be walked to another file), and images are raster files
 * by bare name only.
 */

let guide: string;
let images: string;

const INDEX = [
  "# The guide",
  "",
  "Prose that is not an item.",
  "",
  "## Getting going",
  "",
  "- [First week](first-week.md): the daily rhythm.",
  "- [Going live](going-live.md): arming safely.",
  "",
  "## Questions",
  "",
  "- [FAQ](faq.md): short answers.",
  "- not an item",
].join("\n");

beforeEach(() => {
  guide = fs.mkdtempSync(path.join(os.tmpdir(), "cp-guide-"));
  images = fs.mkdtempSync(path.join(os.tmpdir(), "cp-images-"));
  fs.writeFileSync(path.join(guide, "README.md"), INDEX);
  fs.writeFileSync(path.join(guide, "first-week.md"), "# First week\n\n## Mornings\n\n### `run.py status`\n\n```\n## not a heading\n```\n");
  fs.writeFileSync(path.join(guide, "faq.md"), "# FAQ\n");
  fs.writeFileSync(path.join(guide, "secret.md"), "# not listed\n");
  fs.writeFileSync(path.join(images, "shot.png"), Buffer.from([0x89, 0x50, 0x4e, 0x47]));
  fs.writeFileSync(path.join(images, "evil.svg"), "<svg><script/></svg>");
});

describe("parseGuideIndex", () => {
  it("reads groups and items in order and skips prose", () => {
    const groups = parseGuideIndex(INDEX);
    expect(groups.map((g) => g.title)).toEqual(["Getting going", "Questions"]);
    expect(groups[0]!.pages.map((p) => p.slug)).toEqual(["first-week", "going-live"]);
    expect(groups[0]!.pages[1]).toMatchObject({ title: "Going live", description: "arming safely." });
  });
});

describe("readHelpIndex", () => {
  it("adds each page's headings, outside code fences, and none for a missing page", () => {
    const pages = readHelpIndex(guide).groups.flatMap((g) => g.pages);
    expect(pages.find((p) => p.slug === "first-week")!.headings).toEqual(["Mornings", "run.py status"]);
    expect(pages.find((p) => p.slug === "going-live")!.headings).toEqual([]);
  });
  it("reports an unreadable index rather than throwing", () => {
    const r = readHelpIndex(path.join(guide, "nope"));
    expect(r.groups).toEqual([]);
    expect(r.error).not.toBeNull();
  });
});

describe("readHelpPage", () => {
  it("serves a listed page", () => {
    expect(readHelpPage("first-week", guide)).toMatchObject({ slug: "first-week", title: "First week" });
  });
  it("never serves a file the index does not list, or a path", () => {
    expect(readHelpPage("secret", guide)).toBeNull();
    expect(readHelpPage("README", guide)).toBeNull();
    expect(readHelpPage("../README", guide)).toBeNull();
    expect(readHelpPage("going-live", guide)).toBeNull(); // listed, but no file
  });
});

describe("readHelpImage", () => {
  it("serves a raster image by bare name", () => {
    expect(readHelpImage("shot.png", images)?.type).toBe("image/png");
  });
  it("refuses SVG, paths and dotfiles", () => {
    expect(readHelpImage("evil.svg", images)).toBeNull();
    expect(readHelpImage("../shot.png", images)).toBeNull();
    expect(readHelpImage("..\\shot.png", images)).toBeNull();
    expect(readHelpImage(".hidden.png", images)).toBeNull();
    expect(readHelpImage("missing.png", images)).toBeNull();
  });
});

describe("the real docs/guide", () => {
  it("parses, and every listed page is served", () => {
    const idx = readHelpIndex();
    expect(idx.error).toBeNull();
    const pages = idx.groups.flatMap((g) => g.pages);
    expect(pages.length).toBeGreaterThan(0);
    for (const p of pages) expect(readHelpPage(p.slug), `${p.slug} under ${GUIDE_DIR}`).not.toBeNull();
  });
});
