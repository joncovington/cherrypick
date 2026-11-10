import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { headingSlug, markdownHeadings, plainHeading, type HelpIndexGroup } from "@console/shared";
import { GuideMarkdown, guideImage, guideLink } from "../src/pages/Help/GuideMarkdown";
import { filterFaq, parseFaq } from "../src/pages/Help/FaqTab";
import { filterIndex, flatPages } from "../src/pages/Help/GuidesTab";

/**
 * The Help page's pure parts: where a guide's links and images go, GitHub's anchor ids, the FAQ's
 * rows, the searches, and that a guide's raw HTML is never rendered as HTML.
 */

const SLUGS = new Set(["first-week", "faq", "external-reports"]);

describe("guideLink", () => {
  it("sends another guide to its Help page, keeping the anchor", () => {
    expect(guideLink("external-reports.md#posting-to-discord", SLUGS)).toEqual({ kind: "help", to: "/help/external-reports#posting-to-discord" });
    expect(guideLink("./faq.md", SLUGS)).toEqual({ kind: "help", to: "/help/faq" });
  });
  it("keeps an in-page anchor and an absolute URL", () => {
    expect(guideLink("#the-file", SLUGS)).toEqual({ kind: "anchor", to: "#the-file" });
    expect(guideLink("https://example.test/x", SLUGS)).toEqual({ kind: "external", to: "https://example.test/x" });
  });
  it("sends a repo file, or an unlisted guide, to GitHub", () => {
    expect(guideLink("../../INSTALL.md#updating-to-a-new-release", SLUGS).to).toBe(
      "https://github.com/joncovington/cherrypick/blob/main/INSTALL.md#updating-to-a-new-release",
    );
    expect(guideLink("../configuration-and-storage.md", SLUGS).to).toBe("https://github.com/joncovington/cherrypick/blob/main/docs/configuration-and-storage.md");
    expect(guideLink("not-listed.md", SLUGS).to).toBe("https://github.com/joncovington/cherrypick/blob/main/docs/guide/not-listed.md");
  });
});

describe("guideImage", () => {
  it("serves docs/images through the console and leaves anything else alone", () => {
    expect(guideImage("../images/live-today.png")).toBe("/api/help/images/live-today.png");
    expect(guideImage("../images/../secret.png")).toBe("../images/../secret.png");
    expect(guideImage("https://example.test/a.png")).toBe("https://example.test/a.png");
  });
});

describe("headingSlug", () => {
  it("matches GitHub's anchors", () => {
    expect(headingSlug("Posting to Discord")).toBe("posting-to-discord");
    expect(headingSlug("What does \"P&L\" mean here?")).toBe("what-does-pl-mean-here");
    expect(headingSlug("run.py status")).toBe("runpy-status");
    expect(headingSlug("A  B")).toBe("a--b");
    const seen = new Map<string, number>();
    expect([headingSlug("Notes", seen), headingSlug("Notes", seen), headingSlug("Notes", seen)]).toEqual(["notes", "notes-1", "notes-2"]);
  });
  it("keeps a code span as written and drops only emphasis markers", () => {
    expect(plainHeading("Why does it show my account as `****1234`?")).toBe("Why does it show my account as ****1234?");
    expect(plainHeading("The **halt** flag and _arms_ and `snake_case`")).toBe("The halt flag and arms and snake_case");
    expect(plainHeading("See [the file](x.md)")).toBe("See the file");
    expect(headingSlug(plainHeading("Why does it show my account as `****1234`?"))).toBe("why-does-it-show-my-account-as-1234");
  });
  it("reads headings outside code fences only", () => {
    expect(markdownHeadings("## One\n```\n## Two\n```\n### Three `x`\n#### Four")).toEqual([
      { level: 2, text: "One" },
      { level: 3, text: "Three x" },
    ]);
  });
});

const FAQ = [
  "# FAQ",
  "",
  "Short answers.",
  "",
  "## Getting started",
  "",
  "### What is this?",
  "A set of tools.",
  "",
  "### Does it cost anything?",
  "The software is free.",
  "",
  "## Problems",
  "",
  "### What is this?",
  "A repeat, numbered the way GitHub numbers it.",
].join("\n");

describe("parseFaq", () => {
  it("groups questions with their answers and GitHub's ids", () => {
    const f = parseFaq(FAQ);
    expect(f.intro).toBe("Short answers.");
    expect(f.groups.map((g) => g.title)).toEqual(["Getting started", "Problems"]);
    expect(f.groups[0]!.questions[0]).toEqual({ id: "what-is-this", question: "What is this?", answer: "A set of tools." });
    expect(f.groups[1]!.questions[0]!.id).toBe("what-is-this-1");
  });
  it("filters on the question or the answer", () => {
    const f = parseFaq(FAQ);
    expect(filterFaq(f.groups, "free").flatMap((g) => g.questions.map((q) => q.question))).toEqual(["Does it cost anything?"]);
    expect(filterFaq(f.groups, "nothing like it")).toEqual([]);
  });
});

const GROUPS: HelpIndexGroup[] = [
  { title: "Start", pages: [{ slug: "first-week", title: "Your first week", description: "the daily rhythm", headings: ["Mornings", "After the close"] }] },
  { title: "Questions", pages: [{ slug: "faq", title: "FAQ", description: "short answers", headings: ["Problems"] }] },
];

describe("the guide index", () => {
  it("steps through pages in index order", () => {
    expect(flatPages(GROUPS).map((p) => p.slug)).toEqual(["first-week", "faq"]);
  });
  it("searches titles, descriptions and headings", () => {
    expect(filterIndex(GROUPS, "close").flatMap((g) => g.pages.map((p) => p.slug))).toEqual(["first-week"]);
    expect(filterIndex(GROUPS, "answers").flatMap((g) => g.pages.map((p) => p.slug))).toEqual(["faq"]);
    expect(filterIndex(GROUPS, "")).toHaveLength(2);
  });
});

describe("GuideMarkdown", () => {
  const render = (md: string) =>
    renderToString(
      <MemoryRouter>
        <GuideMarkdown markdown={md} slugs={SLUGS} />
      </MemoryRouter>,
    );
  it("never renders a guide's raw HTML as HTML", () => {
    const html = render('Hello <script>alert(1)</script> <img src=x onerror="alert(2)">');
    // Shown as text at most (escaped), never as an element.
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).not.toMatch(/<[^>]*\sonerror=/);
  });
  it("drops a javascript: link", () => {
    expect(render("[x](javascript:alert(1))")).not.toContain("javascript:");
  });
  it("gives headings GitHub's ids and routes guide links in the app", () => {
    const html = render("## The file\n\nSee [posting](external-reports.md#posting-to-discord).");
    expect(html).toContain('id="the-file"');
    expect(html).toContain('href="/help/external-reports#posting-to-discord"');
  });
  it("makes a screenshot a button that opens it full size in a dialog", () => {
    const html = render("![Live today](../images/live-today.png)");
    expect(html).toMatch(/<button[^>]*class="guide-img-btn"[^>]*aria-label="Expand image: Live today"/);
    expect(html).toMatch(/<dialog[^>]*class="guide-img-dialog"/);
    expect(html.match(/src="\/api\/help\/images\/live-today.png"/g)).toHaveLength(2);
  });
  it("draws GitHub tables", () => {
    expect(render("| a | b |\n|---|---|\n| 1 | 2 |")).toContain("<table>");
  });
});
