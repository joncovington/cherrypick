import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { headingSlug, plainHeading } from "@console/shared";
import { useHelpIndex, useHelpPage } from "../../lib/api";
import { GuideMarkdown } from "./GuideMarkdown";

export interface FaqQuestion {
  id: string;
  question: string;
  answer: string;
}

export interface FaqGroup {
  title: string;
  questions: FaqQuestion[];
}

/**
 * docs/guide/faq.md as rows: `##` groups, `###` questions, the markdown under each question its
 * answer. Ids are the questions' GitHub anchors (numbered across the page the way GitHub numbers
 * repeats), so `/help/faq#<id>` and a link on GitHub land on the same row.
 */
export function parseFaq(markdown: string): { intro: string; groups: FaqGroup[] } {
  const groups: FaqGroup[] = [];
  const intro: string[] = [];
  const seen = new Map<string, number>();
  let current: FaqQuestion | null = null;
  let fence = false;
  for (const line of markdown.split(/\r?\n/)) {
    if (/^\s*(```|~~~)/.test(line)) fence = !fence;
    const h = fence ? null : /^(#{1,3}) (.+?)\s*$/.exec(line);
    if (h && h[1] === "#") continue;
    if (h && h[1] === "##") {
      headingSlug(plainHeading(h[2]!), seen);
      groups.push({ title: plainHeading(h[2]!), questions: [] });
      current = null;
      continue;
    }
    if (h && h[1] === "###" && groups.length > 0) {
      const text = plainHeading(h[2]!);
      current = { id: headingSlug(text, seen), question: text, answer: "" };
      groups[groups.length - 1]!.questions.push(current);
      continue;
    }
    if (current !== null) current.answer += `${line}\n`;
    else if (groups.length === 0) intro.push(line);
  }
  for (const g of groups) for (const q of g.questions) q.answer = q.answer.trim();
  return { intro: intro.join("\n").trim(), groups };
}

/** The groups after a filter: a question stays when the words appear in it or its answer. */
export function filterFaq(groups: readonly FaqGroup[], query: string): FaqGroup[] {
  const q = query.trim().toLowerCase();
  if (q === "") return [...groups];
  return groups
    .map((g) => ({ ...g, questions: g.questions.filter((x) => x.question.toLowerCase().includes(q) || x.answer.toLowerCase().includes(q)) }))
    .filter((g) => g.questions.length > 0);
}

/**
 * Help → faq: every question a collapsible row, grouped, with a filter, expand-all, and deep links
 * (`/help/faq#<question>`) that open the row they name.
 */
export function FaqTab() {
  const { hash } = useLocation();
  const index = useHelpIndex();
  const page = useHelpPage("faq");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Set<string>>(() => new Set(hash ? [decodeURIComponent(hash.slice(1))] : []));
  const slugs = useMemo(() => new Set((index.data?.groups ?? []).flatMap((g) => g.pages.map((p) => p.slug))), [index.data]);
  const faq = useMemo(() => (page.data ? parseFaq(page.data.markdown) : null), [page.data]);

  // A deep link opens its row and scrolls to it, including a hash change while on the page.
  useEffect(() => {
    if (hash === "" || faq === null) return;
    const id = decodeURIComponent(hash.slice(1));
    setOpen((s) => new Set([...s, id]));
    requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ block: "start" }));
  }, [hash, faq]);

  if (page.isError) return <div className="card">The FAQ is not in this checkout's guide index.</div>;
  if (faq === null) return null;

  const shown = filterFaq(faq.groups, query);
  const all = faq.groups.flatMap((g) => g.questions.map((q) => q.id));
  // A filter opens what it finds: the reader is looking for an answer, not a question.
  const isOpen = (id: string) => query.trim() !== "" || open.has(id);
  const toggle = (id: string) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  return (
    <div className="faq">
      {faq.intro !== "" && <GuideMarkdown markdown={faq.intro} slugs={slugs} />}
      <div className="faq-controls">
        <input type="search" className="guide-search" placeholder="filter the questions" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="filter the questions" />
        <button type="button" className="mode-btn" onClick={() => setOpen(new Set(all))}>
          expand all
        </button>
        <button type="button" className="mode-btn" onClick={() => setOpen(new Set())}>
          collapse all
        </button>
      </div>
      {shown.length === 0 && <div className="muted">No question mentions “{query}”.</div>}
      {shown.map((g) => (
        <section key={g.title} className="faq-group">
          <h2 className="faq-group-title">{g.title}</h2>
          {g.questions.map((q) => (
            <div key={q.id} id={q.id} className={`faq-row ${isOpen(q.id) ? "open" : ""}`}>
              <button type="button" className="faq-q" aria-expanded={isOpen(q.id)} onClick={() => toggle(q.id)}>
                <span className="faq-caret">{isOpen(q.id) ? "▾" : "▸"}</span>
                {q.question}
                <a className="guide-anchor" href={`#${q.id}`} aria-label="link to this question" onClick={(e) => e.stopPropagation()}>
                  #
                </a>
              </button>
              {isOpen(q.id) && (
                <div className="faq-a">
                  <GuideMarkdown markdown={q.answer} slugs={slugs} />
                </div>
              )}
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
