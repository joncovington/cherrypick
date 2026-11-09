/**
 * The Help page: the user guides in `docs/guide/`, ordered and grouped by `docs/guide/README.md`.
 * Only a page the index lists is served; `tools/check_guide.py` keeps the index and the files in step.
 */
export interface HelpIndexPage {
  /** The file name without `.md`: the page's URL is `/help/<slug>`. */
  slug: string;
  title: string;
  description: string;
  /** The page's `##` and `###` headings as plain text, for the sidebar's search. */
  headings: string[];
}

export interface HelpIndexGroup {
  title: string;
  pages: HelpIndexPage[];
}

export interface HelpIndexPayload {
  groups: HelpIndexGroup[];
  /** Null when the index cannot be read (a checkout without docs/guide). */
  error: string | null;
}

export interface HelpPagePayload {
  slug: string;
  title: string;
  markdown: string;
}

/** A heading's text as a reader sees it: link syntax, emphasis markers and code backticks removed.
 *  A code span keeps its content as written (`****1234` stays four asterisks and four digits). */
export function plainHeading(text: string): string {
  return text
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .split(/(`[^`]*`)/)
    .map((part) =>
      part.startsWith("`") && part.endsWith("`") && part.length >= 2
        ? part.slice(1, -1)
        : part.replace(/(\*\*|__)(?=\S)(.+?)(?<=\S)\1/g, "$2").replace(/(\*|_)(?=\S)(.+?)(?<=\S)\1/g, "$2"),
    )
    .join("")
    .trim();
}

/** The `##`/`###` headings of a markdown page, outside fenced code, with their level. */
export function markdownHeadings(markdown: string): Array<{ level: 2 | 3; text: string }> {
  const out: Array<{ level: 2 | 3; text: string }> = [];
  let fence = false;
  for (const line of markdown.split(/\r?\n/)) {
    if (/^\s*(```|~~~)/.test(line)) fence = !fence;
    if (fence) continue;
    const m = /^(##|###) (.+?)\s*#*\s*$/.exec(line);
    if (m) out.push({ level: m[1] === "##" ? 2 : 3, text: plainHeading(m[2]!) });
  }
  return out;
}

/** A heading's anchor id, as GitHub makes it, so a link that works on GitHub works on the Help page:
 *  lower case, punctuation other than hyphens dropped, spaces to hyphens. `seen` numbers repeats
 *  (`-1`, `-2`) the way GitHub does; pass one map per page. */
export function headingSlug(text: string, seen?: Map<string, number>): string {
  const base = text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, "")
    .replace(/ /g, "-");
  if (seen === undefined) return base;
  const n = seen.get(base) ?? 0;
  seen.set(base, n + 1);
  return n === 0 ? base : `${base}-${String(n)}`;
}
