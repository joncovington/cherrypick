import type { ReactNode } from "react";

/**
 * The narrative note, rendered as elements.
 *
 * Deliberately builds React nodes rather than setting HTML. The note is model-generated text read
 * off disk, so putting it through `dangerouslySetInnerHTML` would make any prose the agent emits —
 * or anything that ever lands in that file — executable in this page. Building elements makes that
 * structurally impossible, and React escapes the text for free.
 *
 * Deliberately tiny, and not a markdown library. The input is our own prompt's output with a known,
 * narrow shape: paragraphs, `**bold**` spans, one `## Recommendations` heading, and `- ` bullets.
 * Anything richer renders as plain text rather than being half-parsed, which is the failure mode a
 * hand-rolled markdown parser usually has.
 */

/** `**bold**` spans; everything else is literal text. */
function inline(text: string, keyPrefix: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) => {
    const key = `${keyPrefix}-${i}`;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    return <span key={key}>{part}</span>;
  });
}

export function NoteMarkdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];

  const flushBullets = () => {
    if (bullets.length === 0) return;
    blocks.push(
      <ul className="findings" key={`ul-${blocks.length}`}>
        {bullets.map((b, i) => (
          <li key={i}>{inline(b, `b${blocks.length}-${i}`)}</li>
        ))}
      </ul>,
    );
    bullets = [];
  };

  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    // The file's own header block — the page already states the provenance above it.
    if (line.startsWith("# ") || line.startsWith("---") || line.trim() === "") {
      flushBullets();
      continue;
    }
    if (line.startsWith("- ")) {
      bullets.push(line.slice(2));
      continue;
    }
    flushBullets();
    if (line.startsWith("## ")) {
      blocks.push(<h3 key={`h-${blocks.length}`}>{line.slice(3)}</h3>);
      continue;
    }
    // The generated header carries the provenance in italics; the page says it already.
    if (line.startsWith("_") && line.endsWith("_")) continue;
    blocks.push(<p key={`p-${blocks.length}`}>{inline(line, `p${blocks.length}`)}</p>);
  }
  flushBullets();

  return <div className="note">{blocks}</div>;
}
