/**
 * The Help page's files: the user guides in the checkout's `docs/guide/`, and their images in
 * `docs/images/`. Like the System page's git reads, this reaches into the repo rather than
 * `~/.cherrypick`, read-only.
 *
 * The index (`docs/guide/README.md`) is the allow-list: a slug it does not list is a 404, so the
 * route can never be walked to another file. Images are served only by a plain file name with a
 * raster extension (no SVG, which could carry script on this origin).
 */

import fs from "node:fs";
import path from "node:path";
import { markdownHeadings, type HelpIndexGroup, type HelpIndexPayload, type HelpPagePayload } from "@console/shared";
import { REPO_ROOT } from "../config.js";

export const GUIDE_DIR = path.join(REPO_ROOT, "docs", "guide");
export const IMAGE_DIR = path.join(REPO_ROOT, "docs", "images");

const ITEM = /^- \[([^\]]+)\]\(([a-z0-9-]+)\.md\):\s*(.+)$/;

/** `## Group` headings, each followed by `- [Title](slug.md): description.` items. Anything else is prose. */
export function parseGuideIndex(text: string): HelpIndexGroup[] {
  const groups: HelpIndexGroup[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trimEnd();
    const h = /^## (.+)$/.exec(line);
    if (h) {
      groups.push({ title: h[1]!.trim(), pages: [] });
      continue;
    }
    const m = ITEM.exec(line);
    if (m && groups.length > 0) groups[groups.length - 1]!.pages.push({ title: m[1]!, slug: m[2]!, description: m[3]!.trim(), headings: [] });
  }
  return groups.filter((g) => g.pages.length > 0);
}

/** The index, each page with its headings for the sidebar's search (a missing page has none). */
export function readHelpIndex(dir = GUIDE_DIR): HelpIndexPayload {
  try {
    const groups = parseGuideIndex(fs.readFileSync(path.join(dir, "README.md"), "utf-8"));
    for (const page of groups.flatMap((g) => g.pages)) {
      try {
        page.headings = markdownHeadings(fs.readFileSync(path.join(dir, `${page.slug}.md`), "utf-8")).map((h) => h.text);
      } catch {
        page.headings = [];
      }
    }
    return { groups, error: null };
  } catch (err) {
    return { groups: [], error: err instanceof Error ? (err.message.split("\n")[0] ?? "unreadable") : "unreadable" };
  }
}

/** The page's markdown, or null when the index does not list the slug or the file is missing. */
export function readHelpPage(slug: string, dir = GUIDE_DIR): HelpPagePayload | null {
  const page = readHelpIndex(dir).groups.flatMap((g) => g.pages).find((p) => p.slug === slug);
  if (page === undefined) return null;
  try {
    return { slug, title: page.title, markdown: fs.readFileSync(path.join(dir, `${slug}.md`), "utf-8") };
  } catch {
    return null;
  }
}

const IMAGE_TYPES: Record<string, string> = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp" };

/** A docs image by bare file name, or null for anything that is not one. */
export function readHelpImage(name: string, dir = IMAGE_DIR): { body: Buffer; type: string } | null {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name)) return null;
  const type = IMAGE_TYPES[path.extname(name).toLowerCase()];
  if (type === undefined) return null;
  try {
    return { body: fs.readFileSync(path.join(dir, name)), type };
  } catch {
    return null;
  }
}
